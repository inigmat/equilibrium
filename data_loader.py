import os
import tempfile
from datetime import date

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


# ---------------------------------------------------------------------------
# MS Project / MPXJ loader
# ---------------------------------------------------------------------------

def _java_dt_to_date(java_dt):
    """Convert a Java LocalDateTime (via JPype) to a Python date, or None."""
    if java_dt is None:
        return None
    try:
        return date(java_dt.getYear(), java_dt.getMonthValue(),
                    java_dt.getDayOfMonth())
    except Exception:
        return None


def load_and_prepare_mpp(uploaded_file):
    """
    Read an MS Project file (.mpp, .mspdi, .xml, .mpx) via mpxj and return
    the same DataFrame schema used by the XER pipeline.

    Returns
    -------
    tasks_df        : DataFrame
    rels_df         : DataFrame
    mile_mask       : boolean Series
    data_date       : date or None   (MS Project "Status Date")
    project_start   : date or None
    task_res_map    : dict {task_uid: resource_name}  from resource assignments
    """
    # mpxj import is deferred so XER workflow works without a JVM installed.
    # startJVM() must be called explicitly before using org.mpxj Java classes.
    try:
        import mpxj as _mpxj  # noqa: PLC0415, I001
        if not _mpxj.isJVMStarted():
            _mpxj.startJVM()
        from org.mpxj import TimeUnit  # type: ignore  # noqa: PLC0415, I001
        from org.mpxj.reader import UniversalProjectReader  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "MS Project file support requires mpxj and Java 11+. "
            "Install mpxj via pip and ensure a JDK is present."
        ) from exc

    # Write uploaded bytes to a temp file — mpxj needs a file path
    suffix = '.' + uploaded_file.name.rsplit('.', 1)[-1]
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded_file.read())
            tmp_path = tmp.name

        project = UniversalProjectReader().read(tmp_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    props = project.getProjectProperties()

    # Project start & status date
    project_start = _java_dt_to_date(props.getStartDate())
    status_dt = props.getStatusDate()
    data_date = _java_dt_to_date(status_dt)

    # Default calendar hours/day (fallback = 8)
    default_cal = project.getDefaultCalendar()
    default_hours = (
        float(str(default_cal.getMinutesPerDay())) / 60.0
        if default_cal else 8.0
    )

    # Relation type string → 2-char code
    _rel_map = {
        'FINISH_START': 'FS',
        'START_START':  'SS',
        'FINISH_FINISH': 'FF',
        'START_FINISH': 'SF',
    }

    tasks_data = []
    task_res_map = {}
    seen_rels = set()
    rels_data = []

    for task in project.getTasks():
        name = task.getName()
        if name is None:
            continue  # skip the implicit root summary task
        if bool(task.getSummary()):
            continue  # skip WBS/hammock summary tasks

        uid = int(str(task.getUniqueID()))

        # Derive status from percent-complete and actual start
        pct = task.getPercentageComplete()
        actual_start_java = task.getActualStart()
        actual_finish_java = task.getActualFinish()
        pct_val = float(str(pct)) if pct is not None else 0.0

        if pct_val >= 100.0:
            status = 'TaskStatus.TK_Complete'
        elif actual_start_java is not None:
            status = 'TaskStatus.TK_Active'
        else:
            status = 'TaskStatus.TK_NotStart'

        # Effective calendar hours/day for this task
        try:
            cal = task.getEffectiveCalendar()
            hours_per_day = float(str(cal.getMinutesPerDay())) / 60.0
        except Exception:
            hours_per_day = default_hours
        if hours_per_day <= 0:
            hours_per_day = 8.0

        # Duration: remaining for active/complete, target for not-started
        if status == 'TaskStatus.TK_NotStart':
            dur_obj = task.getDuration()
        else:
            dur_obj = task.getRemainingDuration() or task.getDuration()

        duration_days = 0.0
        if dur_obj is not None:
            try:
                dur_hours = float(str(
                    dur_obj.convertUnits(TimeUnit.HOURS, props).getDuration()
                ))
                duration_days = dur_hours / hours_per_day
            except Exception:
                duration_days = 0.0

        # Milestone detection
        is_milestone = bool(task.getMilestone())
        task_type = 'TK_Milestone' if is_milestone else 'TK_Task'

        # First resource assignment → used for Scenario 2
        assignments = task.getResourceAssignments()
        if assignments and len(assignments) > 0:
            first_res = assignments[0].getResource()
            if first_res is not None and first_res.getName() is not None:
                task_res_map[uid] = str(first_res.getName())

        tasks_data.append({
            'task_id':   uid,
            'task_code': str(task.getID()),
            'task_name': str(name),
            'task_type': task_type,
            'duration':  duration_days,
            'wbs_id':    None,
            'status':    status,
            'act_start': _java_dt_to_date(actual_start_java),
            'act_end':   _java_dt_to_date(actual_finish_java),
        })

        # Collect relationships (predecessors of this task)
        preds = task.getPredecessors()
        if not preds:
            continue
        for rel in preds:
            pred_uid = int(str(rel.getPredecessorTask().getUniqueID()))
            succ_uid = int(str(rel.getSuccessorTask().getUniqueID()))
            key = (pred_uid, succ_uid)
            if key in seen_rels:
                continue
            seen_rels.add(key)

            rel_type_str = str(rel.getType())
            link_type = next(
                (v for k, v in _rel_map.items() if k in rel_type_str), 'FS'
            )

            lag_days = 0.0
            lag_obj = rel.getLag()
            if lag_obj is not None:
                try:
                    lag_hours = float(str(
                        lag_obj.convertUnits(
                            TimeUnit.HOURS, props
                        ).getDuration()
                    ))
                    lag_days = lag_hours / default_hours
                except Exception:
                    lag_days = 0.0

            rels_data.append({
                'task_id':      succ_uid,
                'pred_task_id': pred_uid,
                'link':         link_type,
                'lag':          lag_days,
            })

    tasks_df = pd.DataFrame(tasks_data)
    rels_df = pd.DataFrame(rels_data)
    mile_mask = tasks_df['task_type'].str.contains('Mile', na=False)

    return tasks_df, rels_df, mile_mask, data_date, project_start, task_res_map
