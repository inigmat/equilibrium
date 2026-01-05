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
    for encoding in ['cp1251', 'windows-1252', 'utf-8']:
        try:
            content = content_bytes.decode(encoding, errors='ignore')
            break
        except UnicodeDecodeError:
            continue

    if content is None:
        # Fallback error, though 'errors='ignore' should prevent this
        raise ValueError("Could not determine file encoding.")

    xer = Xer(content)
    # Get the first project from the file
    project = next(iter(xer.projects.values()))
    return xer, project


def prepare_dataframes(project):
    """
    Extracts relationship, calendar, and task data into DataFrames.
    Common part for both scenarios.
    """
    # --- 1. Relationships ---
    rels_data = [
        {
            'task_id': rel.task_id,
            'lag': rel.lag,
            'link': rel.link,
            'pred_task_id': rel.pred_task_id,
        }
        for rel in project.relationships
    ]
    rels_df = pd.DataFrame(rels_data)

    # --- 2. Calendars ---
    # Map calendar name to daily hours
    calendar_hours = {cal.name: int(cal.day_hr_cnt)
                      for cal in project.calendars}

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
            'wbs_id': task.wbs_id
        })

    tasks_df = pd.DataFrame(tasks_data)

    # Mask to identify milestones
    mile_mask = tasks_df['task_type'].str.contains('Mile', na=False)

    return tasks_df, rels_df, mile_mask
