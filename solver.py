import math
from collections import defaultdict

import pandas as pd
from ortools.sat.python import cp_model

DEFAULTUDFLABEL = "ResAllocation"


def solve_model_common_setup(tasks_df, rels_df,
                             project_start=None, data_date=None):
    """
    Sets up the basic CP-SAT model: time variables and logical dependencies.

    Completed tasks are pinned to their actual dates.
    Active (in-progress) tasks are pinned: actual start,
    end = data_date + remaining.
    Not-started tasks are constrained to start no earlier than data_date.
    """
    model = cp_model.CpModel()

    # Compute the data-date offset in days from project start
    data_date_offset = 0
    if project_start is not None and data_date is not None:
        data_date_offset = max(0, (data_date - project_start).days)

    # Horizon: data_date anchor + worst-case remaining work + buffer
    horizon = data_date_offset + int(tasks_df['duration'].sum()) + 100
    task_vars = {}

    # Create variables for each task
    for _, row in tasks_df.iterrows():
        t_id = row['task_id']
        status = str(row.get('status', ''))
        is_complete = 'TK_Complete' in status
        is_active = 'TK_Active' in status
        # A task is "fixed" (outside the optimizer) if it is already done or
        # currently in progress AND we have a project_start reference date.
        is_fixed = (is_complete or is_active) and project_start is not None

        start_var = model.NewIntVar(0, horizon, f'start_{t_id}')
        end_var = model.NewIntVar(0, horizon, f'end_{t_id}')

        if is_fixed:
            act_start = row.get('act_start')
            act_end = row.get('act_end')

            # Actual start → day offset
            if act_start is not None:
                act_start_dt = (act_start.date()
                                if hasattr(act_start, 'date') else act_start)
                fixed_start = max(0, (act_start_dt - project_start).days)
            else:
                fixed_start = 0

            if is_complete and act_end is not None:
                # Completed: pinned to actual finish date
                act_end_dt = (act_end.date()
                              if hasattr(act_end, 'date') else act_end)
                fixed_end = max(fixed_start, (act_end_dt - project_start).days)
            else:
                # Active: will finish at data_date + remaining duration
                remaining = int(round(row['duration']))
                if remaining == 0 and row['duration'] > 0:
                    remaining = 1
                fixed_end = data_date_offset + remaining

            actual_duration = max(0, fixed_end - fixed_start)
            model.NewIntervalVar(
                start_var, actual_duration, end_var, f'interval_{t_id}'
            )
            model.Add(start_var == fixed_start)
            model.Add(end_var == fixed_end)

            task_vars[t_id] = {
                'start': start_var,
                'end': end_var,
                'duration': actual_duration,
                'fixed': True,
                'is_complete': is_complete,
            }
        else:
            # Not-started (or fixed-tasks when no project_start provided)
            duration = int(round(row['duration']))
            # Prevent non-zero durations from rounding to 0 (e.g. 0.4 days)
            if duration == 0 and row['duration'] > 0:
                duration = 1

            model.NewIntervalVar(
                start_var, duration, end_var, f'interval_{t_id}'
            )

            # Cannot start before the data date
            if data_date_offset > 0:
                model.Add(start_var >= data_date_offset)

            task_vars[t_id] = {
                'start': start_var,
                'end': end_var,
                'duration': duration,
                'fixed': False,
            }

    # Add dependencies based on link types (FS, SS, FF, SF)
    if not rels_df.empty:
        for _, row in rels_df.iterrows():
            pred_id = row['pred_task_id']
            succ_id = row['task_id']
            lag = int(round(row['lag'])) if pd.notna(row['lag']) else 0
            link_type = row['link']

            if pred_id in task_vars and succ_id in task_vars:
                p = task_vars[pred_id]
                s = task_vars[succ_id]

                # Skip constraints where successor is already fixed —
                # completed/active tasks have pinned dates that cannot change.
                if s.get('fixed'):
                    continue

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
    Respects resource (no-overlap) constraints when moving tasks.
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

    # Build resource schedule for overlap checking
    resource_tasks = defaultdict(list)
    for t_id, data in res_dict.items():
        res = data.get('resource')
        if res:
            resource_tasks[res].append(
                (t_id, data['start_day'], data['end_day'])
            )

    def has_resource_overlap(task_id, resource, new_start, new_end):
        """Check if [new_start, new_end) overlaps tasks on same resource."""
        for other_id, other_start, other_end in resource_tasks[resource]:
            if other_id != task_id:
                if new_start < other_end and new_end > other_start:
                    return True
        return False

    processed_results = []
    for t_id, data in res_dict.items():
        # Fixed tasks (completed / active) must never have their dates adjusted
        if data.get('fixed', False):
            processed_results.append({'task_id': t_id, **data})
            continue

        # Only adjust tasks with NO successors (floating tasks)
        if t_id not in tasks_with_successors:
            preds = preds_by_task.get(t_id, [])
            if preds:
                possible_starts = []
                for p_row in preds:
                    p_id = p_row['pred_task_id']
                    lag = (int(round(p_row['lag']))
                           if pd.notna(p_row['lag']) else 0)
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
                    new_start = max(0, max(possible_starts))
                    # Only update if earlier AND no resource overlap
                    if new_start < data['start_day']:
                        new_end = new_start + data['duration']
                        resource = data.get('resource', '')
                        if not resource or not has_resource_overlap(
                            t_id, resource, new_start, new_end
                        ):
                            # Update resource schedule tracking
                            old_entry = (
                                t_id, data['start_day'], data['end_day']
                            )
                            if resource and (
                                old_entry in resource_tasks[resource]
                            ):
                                resource_tasks[resource].remove(old_entry)
                                resource_tasks[resource].append(
                                    (t_id, new_start, new_end)
                                )
                            data['start_day'] = new_start
                            data['end_day'] = new_end

        processed_results.append({'task_id': t_id, **data})

    return pd.DataFrame(processed_results)


def run_scenario_type_1(tasks_df, rels_df, mile_mask, nb_workers,
                        project_start=None, data_date=None):
    """
    Scenario 1: Auto-assign tasks to N interchangeable workers.

    Completed / in-progress tasks are excluded from worker assignment;
    only not-started tasks are optimized.
    """
    model, task_vars, horizon = solve_model_common_setup(
        tasks_df, rels_df, project_start, data_date
    )
    workers = list(range(nb_workers))
    non_miles_ids = tasks_df[~mile_mask]['task_id'].tolist()

    # Only optimise tasks that are not already fixed (completed / active)
    assignable_ids = [
        t_id for t_id in non_miles_ids
        if not task_vars[t_id].get('fixed', False)
    ]

    worker_assignment = {}
    worker_intervals = defaultdict(list)

    for t_id in assignable_ids:
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
            if vars_.get('fixed', False):
                res_name = (
                    "Completed" if vars_.get('is_complete') else "In Progress"
                )
            elif t_id in assignable_ids:
                res_name = "Unassigned"
                for w in workers:
                    if solver.Value(worker_assignment[(t_id, w)]):
                        res_name = f"Worker {w+1}"
                        break
            else:
                res_name = "Milestone"

            results.append({
                'task_id': t_id,
                'start_day': solver.Value(vars_['start']),
                'end_day': solver.Value(vars_['end']),
                'resource': res_name,
                'duration': vars_['duration'],
                'fixed': vars_.get('fixed', False),
            })

        final_df = post_process_floating_tasks(pd.DataFrame(results), rels_df)
        return status, solver.Value(makespan), final_df

    return status, None, None


def run_scenario_type_2(tasks_df, rels_df, xer=None, project=None,
                        udf_label=DEFAULTUDFLABEL, subcrew_config=None,
                        project_start=None, data_date=None,
                        task_res_map=None):
    """
    Scenario 2: Assignment based on resource mapping and sub-crew capacity.

    For XER files, the resource mapping is derived from a P6 UDF field.
    For MPP files, pass task_res_map directly (uid -> resource_name) to
    bypass the UDF lookup.

    Completed / in-progress tasks are pinned to actual dates and excluded
    from sub-crew assignment; only not-started tasks are optimized.
    """
    if subcrew_config is None:
        subcrew_config = {}

    if task_res_map is None:
        # XER / P6 path: extract resource mapping from UDF
        res_udf = next(
            (el for el in xer.udf_types.values() if el.label == udf_label),
            None,
        )
        if not res_udf:
            return "UDF_NOT_FOUND", None, None

        task_res_map = {
            t.uid: t.user_defined_fields[res_udf]
            for t in project.tasks if res_udf in t.user_defined_fields
        }

    model, task_vars, horizon = solve_model_common_setup(
        tasks_df, rels_df, project_start, data_date
    )
    subcrew_intervals = defaultdict(list)
    sub_assignment = {}
    # resource -> list of (t_id, duration) for load-balancing
    resource_assignable = defaultdict(list)

    for t_id, resource in task_res_map.items():
        if t_id not in task_vars:
            continue
        # Fixed tasks (completed / active) are already pinned — skip assignment
        if task_vars[t_id].get('fixed', False):
            continue

        nb_subs = subcrew_config.get(resource, 1)
        duration = task_vars[t_id]['duration']
        if duration == 0:
            continue

        resource_assignable[resource].append((t_id, duration))

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

    # Workload balance: cap each sub-crew at avg_workload + max_task_duration
    # so no single sub-crew gets a disproportionate share of the total work.
    for resource, tasks in resource_assignable.items():
        nb_subs = subcrew_config.get(resource, 1)
        if nb_subs <= 1:
            continue
        total_dur = sum(d for _, d in tasks)
        max_dur = max(d for _, d in tasks)
        cap = math.ceil(total_dur / nb_subs) + max_dur
        for s in range(nb_subs):
            terms = [
                d * sub_assignment[(t_id, resource, s)]
                for t_id, d in tasks
                if (t_id, resource, s) in sub_assignment
            ]
            if terms:
                model.Add(sum(terms) <= cap)

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

            if vars_.get('fixed', False):
                # Retain the UDF resource name with a status suffix
                is_done = vars_.get('is_complete')
                suffix = "Completed" if is_done else "In Progress"
                if base_res != "Unassigned / Milestone":
                    res_name = f"{base_res} ({suffix})"
                else:
                    res_name = suffix
            elif t_id in task_res_map and vars_['duration'] > 0:
                res_name = base_res  # fallback if no sub found
                nb_subs = subcrew_config.get(base_res, 1)
                for s in range(nb_subs):
                    key = (t_id, base_res, s)
                    if solver.Value(sub_assignment.get(key, 0)):
                        res_name = f"{base_res} - Sub {s+1}"
                        break
            else:
                res_name = base_res  # "Unassigned / Milestone"

            results.append({
                'task_id': t_id,
                'start_day': solver.Value(vars_['start']),
                'end_day': solver.Value(vars_['end']),
                'resource': res_name,
                'duration': vars_['duration'],
                'fixed': vars_.get('fixed', False),
            })

        final_df = post_process_floating_tasks(pd.DataFrame(results), rels_df)
        return status, solver.Value(makespan), final_df

    return status, None, None
