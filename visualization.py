import matplotlib.pyplot as plt
import pandas as pd
import io

DATEFORMAT = '%Y-%m-%d'


def plot_gantt_chart(schedule_df, tasks_info_df):
    """
    Generates a Matplotlib Gantt chart.
    Improved filtering to exclude milestones explicitly (Fix for Issue 2).
    """
    # Merge results with task details
    df = schedule_df.merge(
        tasks_info_df[['task_id', 'task_code', 'task_name', 'task_type']], # Включаем task_type для фильтрации
        on='task_id',
        how='left'
        )

    # Фильтруем:
    # 1. Вехи (Milestones) - они не являются ресурсами для Ганта.
    df = df[~df['task_type'].str.contains('Mile', na=False)] 
    
    # 2. Неназначенные задачи
    df = df[~df['resource'].str.contains('Unassigned', na=False)]
    
    df = df.dropna(subset=['resource'])

    if df.empty:
        return None

    fig, ax = plt.subplots(figsize=(14, 8))

    # Group by resource for the Y-axis
    resources = sorted(df['resource'].unique())
    y_map = {res: i for i, res in enumerate(resources)}

    colors = plt.cm.tab20.colors
    bar_height = 0.6

    for idx, row in df.iterrows():
        start = row['start_day']
        duration = row['end_day'] - row['start_day']
        res = row['resource']
        y = y_map[res]

        # Color based on task code hash
        c = colors[hash(row['task_code']) % len(colors)]

        ax.barh(y, duration, left=start, height=bar_height,
                color=c, edgecolor='black', alpha=0.8)

        # Task Code Label
        if duration > 1:
            ax.text(start + duration/2, y, row['task_code'],
                    ha='center', va='center', color='white',
                    fontsize=8, fontweight='bold')

    ax.set_yticks(range(len(resources)))
    ax.set_yticklabels(resources)
    ax.set_xlabel("Days from Project Start")
    ax.set_title("Gantt Chart: Resource Utilization")
    ax.grid(True, axis='x', linestyle='--', alpha=0.5)
    ax.invert_yaxis()  # Display top-down

    return fig


def create_excel_download(schedule_df, tasks_df, project_start_date):
    """
    Prepares the final data structure and creates an Excel file in memory.
    Handles milestones differently - no subtraction of 1 day for milestones.
    FIXED: Syntax error in output_df assignment.
    """
    # Merge with task details including task_type
    full_df = schedule_df.merge(
        tasks_df[['task_id', 'task_code', 'task_name', 'task_type']],
        on='task_id',
        how='left'
    )

    # Convert days to dates
    full_df['Start Date'] = (
        pd.to_datetime(project_start_date)
        + pd.to_timedelta(full_df['start_day'], unit='D')
    )
    # Convert days to dates
    full_df['End Date'] = (
        pd.to_datetime(project_start_date)
        + pd.to_timedelta(full_df['end_day'], unit='D')
    )

    # Prepare output dataframe
    output_df = full_df[
        ['task_code', 'task_name', 'resource', 'Start Date', 'End Date']
        ].sort_values('task_code')

    # Format dates as strings
    output_df['Start Date'] = output_df['Start Date'].dt.strftime(DATEFORMAT)
    output_df['End Date'] = output_df['End Date'].dt.strftime(DATEFORMAT)

    # Create Excel file in memory
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        output_df.to_excel(writer, index=False, sheet_name='Schedule')

    return buffer.getvalue()
