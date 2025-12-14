from ortools.sat.python import cp_model
from collections import defaultdict
import pandas as pd

DEFAULTUDFLABEL = "ResAllocation"


def solve_model_common_setup(tasks_df, rels_df):
    """
    Basic OR-Tools model setup: time variables and task dependencies.
    """
    model = cp_model.CpModel()
    horizon = int(tasks_df['duration'].sum()) + 100  # Safety margin

    task_vars = {}
    # {task_id: {'start': var, 'end': var, 'interval': var, 'duration': int}}

    # Create variables
    for _, row in tasks_df.iterrows():
        t_id = row['task_id']
        duration = int(round(row['duration']))  # OR-Tools requires int

        start_var = model.NewIntVar(0, horizon, f'start_{t_id}')
        end_var = model.NewIntVar(0, horizon, f'end_{t_id}')
        interval_var = model.NewIntervalVar(
            start_var, duration, end_var, f'interval_{t_id}'
            )

        task_vars[t_id] = {
            'start': start_var,
            'end': end_var,
            'interval': interval_var,
            'duration': duration
        }

    # Add dependencies
    if not rels_df.empty:
        for _, row in rels_df.iterrows():
            pred_id = row['pred_task_id']
            succ_id = row['task_id']
            lag = int(row['lag']) if pd.notna(row['lag']) else 0
            link_type = row['link']

            if pred_id in task_vars and succ_id in task_vars:
                pred = task_vars[pred_id]
                succ = task_vars[succ_id]

                if link_type == 'FS':
                    model.Add(succ['start'] >= pred['end'] + lag)
                elif link_type == 'SS':
                    model.Add(succ['start'] >= pred['start'] + lag)
                elif link_type == 'FF':
                    model.Add(succ['end'] >= pred['end'] + lag)
                elif link_type == 'SF':
                    model.Add(succ['end'] >= pred['start'] + lag)

    return model, task_vars, horizon


def run_scenario_type_1(tasks_df, rels_df, mile_mask, nb_workers):
    """
    Scenario 1: Automatic scheduling of tasks across N interchangeable workers.
    """
    model, task_vars, horizon = solve_model_common_setup(tasks_df, rels_df)

    workers = list(range(nb_workers))
    non_miles_ids = tasks_df[~mile_mask]['task_id'].tolist()

    worker_assignment = {}  # {(task_id, worker_id): bool_var}
    worker_intervals = defaultdict(list)

    for t_id in non_miles_ids:
        duration = task_vars[t_id]['duration']

        assigned_bools = []

        for w in workers:
            assign_var = model.NewBoolVar(f'assign_{t_id}_{w}')
            worker_assignment[(t_id, w)] = assign_var
            assigned_bools.append(assign_var)

            # Optional interval: active only if assign_var == 1
            opt_interval = model.NewOptionalIntervalVar(
                task_vars[t_id]['start'], duration, task_vars[t_id]['end'],
                assign_var, f'opt_{t_id}_{w}'
            )
            worker_intervals[w].append(opt_interval)

        # Task must be assigned to exactly one worker
        model.AddExactlyOne(assigned_bools)

    # NoOverlap constraint for each worker
    for w in workers:
        if worker_intervals[w]:
            model.AddNoOverlap(worker_intervals[w])

    # Objective function
    makespan = model.NewIntVar(0, horizon, 'makespan')
    model.AddMaxEquality(makespan, [v['end'] for v in task_vars.values()])
    model.Minimize(makespan)

    # Solver
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20.0
    status = solver.Solve(model)

    results = []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for t_id, vars_ in task_vars.items():
            start_val = solver.Value(vars_['start'])
            end_val = solver.Value(vars_['end'])

            # Determine resource
            resource_name = "Milestone"
            if t_id in non_miles_ids:
                for w in workers:
                    if solver.Value(worker_assignment[(t_id, w)]):
                        resource_name = f"Worker {w+1}"
                        break

            results.append({
                'task_id': t_id,
                'start_day': start_val,
                'end_day': end_val,
                'resource': resource_name
            })
        return status, solver.Value(makespan), pd.DataFrame(results)

    return status, None, None


def run_scenario_type_2(tasks_df, rels_df,
                        xer, project, udf_label=DEFAULTUDFLABEL,
                        subcrew_config={}): 
    """
    Scenario 2: Scheduling based on strictly defined resources in a UDF
    and assigning tasks to a specified number of sub-crews per resource.
    
    Includes fix for issue 2 by explicitly naming Sub-Crew even if N=1.
    """
    # Find the UDF field
    res_udf = None
    for el in xer.udf_types.values():
        if el.label == udf_label:
            res_udf = el
            break

    if not res_udf:
        return "UDF_NOT_FOUND", None, None

    # Collect resource assignments from UDF
    task_res_map = {}
    for task in project.tasks:
        if res_udf in task.user_defined_fields:
            task_res_map[task.uid] = task.user_defined_fields[res_udf]

    model, task_vars, horizon = solve_model_common_setup(tasks_df, rels_df)

    # --- LOGIC FOR SUB-CREWS ---
    
    subcrew_intervals = defaultdict(list)
    subcrew_assignment = {} 
    
    for t_id, resource in task_res_map.items():
        if t_id in task_vars:
            
            nb_subcrews = subcrew_config.get(resource, 1) 
            duration = task_vars[t_id]['duration']
            
            # Skip assignment logic for zero duration tasks (milestones)
            if duration == 0:
                continue

            assigned_bools = []

            for s in range(nb_subcrews):
                subcrew_name = f"{resource} - Sub {s+1}"
                
                assign_var = model.NewBoolVar(f'assign_{t_id}_{s}')
                subcrew_assignment[(t_id, resource, s)] = assign_var
                assigned_bools.append(assign_var)

                # Optional interval: active only if assign_var == 1
                opt_interval = model.NewOptionalIntervalVar(
                    task_vars[t_id]['start'], duration, task_vars[t_id]['end'],
                    assign_var, f'opt_{t_id}_{s}'
                )
                subcrew_intervals[subcrew_name].append(opt_interval)
                
            # Task must be assigned to exactly one sub-crew
            if assigned_bools:
                model.AddExactlyOne(assigned_bools)
            
    # Apply NoOverlap constraint for each *Sub-Crew*
    for subcrew_name, intervals in subcrew_intervals.items():
        if intervals:
            model.AddNoOverlap(intervals)
            

    # Objective function (Minimize makespan)
    makespan = model.NewIntVar(0, horizon, 'makespan')
    model.AddMaxEquality(makespan, [v['end'] for v in task_vars.values()])
    model.Minimize(makespan)

    # Solver
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20.0
    status = solver.Solve(model)

    results = []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for t_id, vars_ in task_vars.items():
            start_val = solver.Value(vars_['start'])
            end_val = solver.Value(vars_['end'])
            duration = task_vars[t_id]['duration']
            
            # Determine the assigned sub-crew or just the main resource/milestone
            base_resource = task_res_map.get(t_id, "Unassigned / Milestone")
            resource_name = base_resource 

            if base_resource in subcrew_config:
                nb_subcrews = subcrew_config.get(base_resource, 1)
                
                if duration > 0:
                    # Operational task: must be assigned a Sub-Crew name
                    
                    if nb_subcrews > 1:
                        # Find the assigned sub-crew
                        for s in range(nb_subcrews):
                            if (t_id, base_resource, s) in subcrew_assignment and \
                               solver.Value(subcrew_assignment[(t_id, base_resource, s)]):
                                
                                resource_name = f"{base_resource} - Sub {s+1}"
                                break
                    elif nb_subcrews == 1:
                        # Only one sub-crew, but assign the explicit name for consistency (Fix for Issue 2)
                        resource_name = f"{base_resource} - Sub 1"
                        
                # If duration == 0, resource_name remains base_resource (Expected for milestones)
            
            results.append({
                'task_id': t_id,
                'start_day': start_val,
                'end_day': end_val,
                'resource': resource_name
            })
        return status, solver.Value(makespan), pd.DataFrame(results)

    return status, None, None