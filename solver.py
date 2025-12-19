import pandas as pd
from collections import defaultdict
from ortools.sat.python import cp_model

DEFAULTUDFLABEL = "ResAllocation"


def solve_model_common_setup(tasks_df, rels_df):
    """
    Sets up the basic CP-SAT model: time variables and logical dependencies.
    """
    model = cp_model.CpModel()
    # Adding safety margin to the horizon
    horizon = int(tasks_df['duration'].sum()) + 100
    task_vars = {}

    # Create variables for each task
    for _, row in tasks_df.iterrows():
        t_id = row['task_id']
        duration = int(round(row['duration']))

        start_var = model.NewIntVar(0, horizon, f'start_{t_id}')
        end_var = model.NewIntVar(0, horizon, f'end_{t_id}')
        # Interval variable links start, duration, and end
        model.NewIntervalVar(start_var, duration, end_var, f'interval_{t_id}')

        task_vars[t_id] = {
            'start': start_var,
            'end': end_var,
            'duration': duration
        }

    # Add dependencies based on link types (FS, SS, FF, SF)
    if not rels_df.empty:
        for _, row in rels_df.iterrows():
            pred_id = row['pred_task_id']
            succ_id = row['task_id']
            lag = int(row['lag']) if pd.notna(row['lag']) else 0
            link_type = row['link']

            if pred_id in task_vars and succ_id in task_vars:
                p = task_vars[pred_id]
                s = task_vars[succ_id]

                if link_type == 'FS':
                    model.Add(s['start'] >= p['end'] + lag)
                elif link_type == 'SS':
                    model.Add(s['start'] >= p['start'] + lag)
                elif link_type == 'FF':
                    model.Add(s['end'] >= p['end'] + lag)
                elif link_type == 'SF':
                    model.Add(s['end'] >= p['start'] + lag)

    return model, task_vars, horizon


def post_process_floating_tasks(results_df, rels_df):
    """
    Recalculates dates for tasks without successors to 'pull' them 
    towards their predecessors, preventing them from floating at the end.
    """
    # Identify tasks that have at least one successor
    tasks_with_successors = (
        set(rels_df['pred_task_id'].unique()) if not rels_df.empty else set()
    )

    # Convert results to dictionary for fast lookup
    res_dict = results_df.set_index('task_id').to_dict('index')

    # Group predecessors by task
    preds_by_task = defaultdict(list)
    if not rels_df.empty:
        for _, row in rels_df.iterrows():
            preds_by_task[row['task_id']].append(row)

    processed_results = []
    for t_id, data in res_dict.items():
        # Only adjust tasks with NO successors (floating tasks)
        if t_id not in tasks_with_successors:
            preds = preds_by_task.get(t_id, [])
            if preds:
                possible_starts = []
                for p_row in preds:
                    p_id = p_row['pred_task_id']
                    lag = int(p_row['lag']) if pd.notna(p_row['lag']) else 0
                    p_data = res_dict.get(p_id)

                    if not p_data:
                        continue

                    # Calculate required start based on link type
                    if p_row['link'] == 'FS':
                        possible_starts.append(p_data['end_day'] + lag)
                    elif p_row['link'] == 'SS':
                        possible_starts.append(p_data['start_day'] + lag)
                    elif p_row['link'] == 'FF':
                        possible_starts.append(
                            p_data['end_day'] + lag - data['duration']
                        )
                    elif p_row['link'] == 'SF':
                        possible_starts.append(
                            p_data['start_day'] + lag - data['duration']
                        )

                if possible_starts:
                    # New start is the earliest possible date given predecessors
                    new_start = max(0, max(possible_starts))
                    # Only update if the new date is earlier than the solver's 
                    if new_start < data['start_day']:
                        data['start_day'] = new_start
                        data['end_day'] = new_start + data['duration']

        processed_results.append({'task_id': t_id, **data})

    return pd.DataFrame(processed_results)


def run_scenario_type_1(tasks_df, rels_df, mile_mask, nb_workers):
    """
    Scenario 1: Auto-assign tasks to N interchangeable workers.
    """
    model, task_vars, horizon = solve_model_common_setup(tasks_df, rels_df)
    workers = list(range(nb_workers))
    non_miles_ids = tasks_df[~mile_mask]['task_id'].tolist()

    worker_assignment = {}
    worker_intervals = defaultdict(list)

    for t_id in non_miles_ids:
        duration = task_vars[t_id]['duration']
        assigned_bools = []
        for w in workers:
            assign_var = model.NewBoolVar(f'assign_{t_id}_{w}')
            worker_assignment[(t_id, w)] = assign_var
            assigned_bools.append(assign_var)

            # Optional interval: exists only if the worker is assigned
            opt_interval = model.NewOptionalIntervalVar(
                task_vars[t_id]['start'], duration, task_vars[t_id]['end'],
                assign_var, f'opt_{t_id}_{w}'
            )
            worker_intervals[w].append(opt_interval)

        model.AddExactlyOne(assigned_bools)

    # Resource constraint: one worker - one task at a time
    for w in workers:
        if worker_intervals[w]:
            model.AddNoOverlap(worker_intervals[w])

    # Objective: Minimize project duration (Makespan)
    makespan = model.NewIntVar(0, horizon, 'makespan')
    model.AddMaxEquality(makespan, [v['end'] for v in task_vars.values()])
    model.Minimize(makespan)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 60.0
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        results = []
        for t_id, vars_ in task_vars.items():
            res_name = "Milestone"
            if t_id in non_miles_ids:
                for w in workers:
                    if solver.Value(worker_assignment[(t_id, w)]):
                        res_name = f"Worker {w+1}"
                        break
            results.append({
                'task_id': t_id,
                'start_day': solver.Value(vars_['start']),
                'end_day': solver.Value(vars_['end']),
                'resource': res_name,
                'duration': vars_['duration']
            })

        final_df = post_process_floating_tasks(pd.DataFrame(results), rels_df)
        return status, solver.Value(makespan), final_df

    return status, None, None


def run_scenario_type_2(tasks_df, rels_df, xer, project,
                        udf_label=DEFAULTUDFLABEL, subcrew_config=None):
    """
    Scenario 2: Assignment based on XER UDF and sub-crew capacity.
    """
    if subcrew_config is None:
        subcrew_config = {}

    # Extract resource mapping from XER UDF
    res_udf = next(
        (el for el in xer.udf_types.values() if el.label == udf_label), None
    )
    if not res_udf:
        return "UDF_NOT_FOUND", None, None

    task_res_map = {
        t.uid: t.user_defined_fields[res_udf]
        for t in project.tasks if res_udf in t.user_defined_fields
    }

    model, task_vars, horizon = solve_model_common_setup(tasks_df, rels_df)
    subcrew_intervals = defaultdict(list)
    sub_assignment = {}

    for t_id, resource in task_res_map.items():
        if t_id in task_vars:
            nb_subs = subcrew_config.get(resource, 1)
            duration = task_vars[t_id]['duration']
            if duration == 0:
                continue

            assigned_bools = []
            for s in range(nb_subs):
                s_name = f"{resource} - Sub {s+1}"
                a_var = model.NewBoolVar(f'assign_{t_id}_{s}')
                sub_assignment[(t_id, resource, s)] = a_var
                assigned_bools.append(a_var)

                subcrew_intervals[s_name].append(
                    model.NewOptionalIntervalVar(
                        task_vars[t_id]['start'], duration,
                        task_vars[t_id]['end'], a_var, f'opt_{t_id}_{s}'
                    )
                )
            if assigned_bools:
                model.AddExactlyOne(assigned_bools)

    for intervals in subcrew_intervals.values():
        if intervals:
            model.AddNoOverlap(intervals)

    makespan = model.NewIntVar(0, horizon, 'makespan')
    model.AddMaxEquality(makespan, [v['end'] for v in task_vars.values()])
    model.Minimize(makespan)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 60.0
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        results = []
        for t_id, vars_ in task_vars.items():
            base_res = task_res_map.get(t_id, "Unassigned / Milestone")
            res_name = base_res
            if t_id in task_res_map and task_vars[t_id]['duration'] > 0:
                nb_subs = subcrew_config.get(base_res, 1)
                for s in range(nb_subs):
                    if solver.Value(sub_assignment.get((t_id, base_res, s), 0)):
                        res_name = f"{base_res} - Sub {s+1}"
                        break
            results.append({
                'task_id': t_id,
                'start_day': solver.Value(vars_['start']),
                'end_day': solver.Value(vars_['end']),
                'resource': res_name,
                'duration': vars_['duration']
            })

        final_df = post_process_floating_tasks(pd.DataFrame(results), rels_df)
        return status, solver.Value(makespan), final_df

    return status, None, None
