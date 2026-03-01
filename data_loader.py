import pandas as pd
from xerparser import Xer


def load_and_parse_xer(uploaded_file):
    """
    Reads the uploaded file, determines encoding,
    and parses it using XerParser.
    Returns the project object and xer object.
    """
    content_bytes = uploaded_file.read()

    # Attempt to decode the file content
    content = None
    for encoding in ['utf-8', 'cp1251', 'windows-1252']:
        try:
            content = content_bytes.decode(encoding)
            break
        except (UnicodeDecodeError, ValueError):
            continue

    if content is None:
        # Fallback: lossy decode if strict decoding fails for all encodings
        content = content_bytes.decode('utf-8', errors='ignore')

    xer = Xer(content)
    # Get the first project from the file
    project = next(iter(xer.projects.values()))
    return xer, project


def prepare_dataframes(project):
    """
    Extracts relationship, calendar, and task data into DataFrames.
    Common part for both scenarios.
    """
    # --- 1. Calendars ---
    # Map calendar name to daily hours
    calendar_hours = {cal.name: int(cal.day_hr_cnt)
                      for cal in project.calendars}

    # --- 2. Relationships ---
    rels_data = []
    for rel in project.relationships:
        # xerparser already provides lag in days
        lag_days = rel.lag if rel.lag else 0

        # Normalize link type (handle 'PR_FS' or enum formats)
        link_raw = str(rel.link)
        link_type = link_raw
        for lt in ['FS', 'SS', 'FF', 'SF']:
            if lt in link_raw:
                link_type = lt
                break

        rels_data.append({
            'task_id': rel.task_id,
            'lag': lag_days,
            'link': link_type,
            'pred_task_id': rel.pred_task_id,
        })
    rels_df = pd.DataFrame(rels_data)

    # --- 3. Tasks ---
    tasks_data = []

    for task in project.tasks:
        # Duration based on status
        if task.status == 'TaskStatus.TK_NotStart':
            duration_hr = task.target_drtn_hr_cnt
        else:
            duration_hr = task.remain_drtn_hr_cnt

        # Convert hours to days
        cal_name = task.calendar.name
        day_hr = calendar_hours.get(cal_name, 8)  # Fallback to 8
        duration_days = duration_hr / day_hr if day_hr > 0 else 0

        tasks_data.append({
            'task_id': task.uid,
            'task_code': task.task_code,
            'task_name': task.name,
            'task_type': str(task.type),
            'duration': duration_days,
            'wbs_id': task.wbs_id,
            'status': str(task.status),
            'act_start': getattr(task, 'act_start_date', None),
            'act_end': getattr(task, 'act_end_date', None),
        })

    tasks_df = pd.DataFrame(tasks_data)

    # Mask to identify milestones
    mile_mask = tasks_df['task_type'].str.contains('Mile', na=False)

    data_date = getattr(project, 'data_date', None)
    return tasks_df, rels_df, mile_mask, data_date
