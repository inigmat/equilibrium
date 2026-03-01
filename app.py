import datetime

import streamlit as st
from ortools.sat.python import cp_model

from data_loader import (
    load_and_parse_xer,
    load_and_prepare_mpp,
    prepare_dataframes,
)
from solver import DEFAULTUDFLABEL, run_scenario_type_1, run_scenario_type_2
from visualization import create_excel_download, plot_gantt_chart

# Constants
MIMECONST = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
MPP_EXTENSIONS = ('.mpp', '.mspdi', '.mpx')

# Streamlit page configuration
st.set_page_config(page_title="Schedule Optimizer", layout="wide")


def _is_mpp(filename: str) -> bool:
    """Return True if the filename looks like an MS Project file."""
    lower = filename.lower()
    # .xml could be MS Project XML (MSPDI) — treat it as MPP when not .xer
    return lower.endswith(MPP_EXTENSIONS) or (
        lower.endswith('.xml') and not lower.endswith('.xer')
    )


def _build_subcrew_ui(tasks_df, task_res_map):
    """
    Render sub-crew number inputs in the sidebar and return
    (unique_resources, subcrew_config).
    """
    unique_resources = sorted(set(task_res_map.values()))
    if not unique_resources:
        return [], {}

    st.sidebar.subheader("Sub-Crew Configuration")
    st.sidebar.caption(
        "Number of parallel sub-crews per brigade. "
        "Default = all tasks can run concurrently (critical path). "
        "Reduce to simulate limited workforce."
    )

    ns_mask = tasks_df['status'] == 'TaskStatus.TK_NotStart'
    ns_task_ids = set(tasks_df[ns_mask]['task_id'])
    resource_task_counts = {}
    for t_id, res in task_res_map.items():
        if t_id in ns_task_ids:
            resource_task_counts[res] = resource_task_counts.get(res, 0) + 1

    subcrew_config = {}
    for resource in unique_resources:
        max_subs = max(1, resource_task_counts.get(resource, 1))
        subcrew_config[resource] = st.sidebar.number_input(
            f"Sub-Crews for **{resource}** ({max_subs} not-started tasks):",
            min_value=1,
            max_value=max_subs,
            value=max_subs,
            step=1,
            key=f"subcrew_{resource}",
        )

    return unique_resources, subcrew_config


def main():
    """
    Main function to run the Streamlit web application.
    """
    st.title("📊 Schedule Optimizer (XER / MS Project)")
    st.sidebar.header("Settings")

    # File uploader — accepts both P6 XER and MS Project formats
    uploaded_file = st.sidebar.file_uploader(
        "Upload Schedule File",
        type=["xer", "mpp", "mspdi", "mpx", "xml"],
    )

    # Project Start Date configuration
    st.sidebar.subheader("Project Start Date")
    use_file_date = st.sidebar.checkbox("Use date from file", value=True)

    project_start = None
    if not use_file_date:
        project_start = st.sidebar.date_input(
            "Select project start date",
            value=datetime.date.today(),
        )

    # Optimization mode selection
    st.sidebar.subheader("Optimization Settings")
    mode = st.sidebar.radio(
        "Select Operation Scenario:",
        (
            "Type 1: Auto-Assignment Optimization",
            "Type 2: Existing Resource Check",
        ),
    )

    # Initialize variables for both modes
    nb_workers = 3
    udf_name = DEFAULTUDFLABEL
    subcrew_config = {}

    # Scenario 1 sidebar controls (always visible)
    if mode == "Type 1: Auto-Assignment Optimization":
        nb_workers = st.sidebar.slider("Number of Workers/Crews", 1, 10, 3)
        st.sidebar.info(
            "The algorithm distributes tasks among the specified "
            "number of workers to minimize project duration (Makespan)."
        )
    else:
        st.sidebar.info(
            "The algorithm minimizes Makespan by respecting existing "
            "resource assignments and optimizes tasks among configured "
            "sub-crews."
        )

    # Process file if uploaded
    if uploaded_file:
        try:
            filename = uploaded_file.name
            mpp_file = _is_mpp(filename)

            # ---------------------------------------------------------------
            # 1. Load and parse the file
            # ---------------------------------------------------------------
            if mpp_file:
                (tasks_df, rels_df, mile_mask,
                 data_date, project_start_file,
                 task_res_map) = load_and_prepare_mpp(uploaded_file)
                xer, project = None, None
            else:
                xer, project = load_and_parse_xer(uploaded_file)
                tasks_df, rels_df, mile_mask, data_date = prepare_dataframes(
                    project
                )
                project_start_file = project.plan_start_date.date()
                task_res_map = None  # built later from P6 UDF

            # Normalise data_date to a plain date object
            if data_date is not None and hasattr(data_date, 'date'):
                data_date = data_date.date()

            if use_file_date:
                project_start = project_start_file

            st.sidebar.info(f"Project start date: **{project_start}**")
            if data_date:
                st.sidebar.info(f"Last Recalc Date: **{data_date}**")

            # ---------------------------------------------------------------
            # 2. Scenario 2 sidebar — resource / sub-crew configuration
            # ---------------------------------------------------------------
            if mode == "Type 2: Existing Resource Check":
                if mpp_file:
                    # Resources come from the file's resource assignments
                    if task_res_map:
                        st.sidebar.caption(
                            "Resources read from MS Project resource assignments."
                        )
                        _, subcrew_config = _build_subcrew_ui(
                            tasks_df, task_res_map
                        )
                    else:
                        st.sidebar.warning(
                            "No resource assignments found in the file. "
                            "Scenario 2 requires tasks with assigned resources."
                        )
                else:
                    # XER: resources come from a UDF field
                    udf_name = st.sidebar.text_input(
                        "UDF Field Name for Crew/Resource",
                        value=DEFAULTUDFLABEL,
                    )

                    res_udf = next(
                        (el for el in xer.udf_types.values()
                         if el.label == udf_name),
                        None,
                    )
                    if res_udf:
                        xer_res_map = {
                            t.uid: t.user_defined_fields[res_udf]
                            for t in project.tasks
                            if res_udf in t.user_defined_fields
                            and t.user_defined_fields[res_udf]
                        }
                        _, subcrew_config = _build_subcrew_ui(
                            tasks_df, xer_res_map
                        )

            # ---------------------------------------------------------------
            # 3. Project overview
            # ---------------------------------------------------------------
            st.subheader("Project Data Overview")
            data_date_str = (
                data_date.strftime('%Y-%m-%d') if data_date else "N/A"
            )
            file_fmt = "MS Project" if mpp_file else "Primavera P6 XER"
            st.write(
                f"**Format:** {file_fmt} | "
                f"**Project Start:** {project_start.strftime('%Y-%m-%d')} | "
                f"**Last Recalc Date:** {data_date_str} | "
                f"**Total Tasks:** {len(tasks_df)}"
            )

            with st.expander("Preview Raw Task Data"):
                st.dataframe(
                    tasks_df[['task_code', 'task_name', 'duration',
                               'task_type']].head()
                )

            # ---------------------------------------------------------------
            # 4. Run Optimization
            # ---------------------------------------------------------------
            if st.button("Run Optimization"):
                with st.spinner("Optimizing schedule..."):
                    if mode == "Type 1: Auto-Assignment Optimization":
                        status, makespan, res_df = run_scenario_type_1(
                            tasks_df, rels_df, mile_mask, nb_workers,
                            project_start, data_date,
                        )
                    else:
                        status, makespan, res_df = run_scenario_type_2(
                            tasks_df, rels_df,
                            xer, project,
                            udf_name, subcrew_config,
                            project_start, data_date,
                            task_res_map=task_res_map,
                        )

                    # 5. Results Display
                    st.subheader("Results")

                    if status == "UDF_NOT_FOUND":
                        st.error(
                            f"UDF field '{udf_name}' not found in the file. "
                            "Please check the spelling."
                        )
                    elif status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                        st.success(
                            f"Solution Found! Minimum Project Duration "
                            f"(Makespan): **{makespan} days**"
                        )

                        tab1, tab2, tab3 = st.tabs([
                            "Gantt Chart",
                            "Schedule Table",
                            "Excel Download",
                        ])

                        with tab1:
                            fig = plot_gantt_chart(res_df, tasks_df)
                            if fig:
                                st.pyplot(fig)
                            else:
                                st.warning("No operational tasks found.")

                        with tab2:
                            display_df = res_df.merge(
                                tasks_df[['task_id', 'task_code', 'task_name']],
                                on='task_id',
                            )
                            if mode == "Type 2: Existing Resource Check":
                                display_df.rename(
                                    columns={'resource': 'Resource/Sub-Crew'},
                                    inplace=True,
                                )
                            res_col = (
                                'Resource/Sub-Crew'
                                if mode == "Type 2: Existing Resource Check"
                                else 'resource'
                            )
                            st.dataframe(
                                display_df[[
                                    'task_code', 'task_name', res_col,
                                    'start_day', 'end_day',
                                ]]
                            )

                        with tab3:
                            excel_data = create_excel_download(
                                res_df, tasks_df, project_start
                            )
                            st.download_button(
                                label="📥 Download Optimized Schedule",
                                data=excel_data,
                                file_name="optimized_schedule.xlsx",
                                mime=MIMECONST,
                            )
                    else:
                        st.error("No optimal solution found.")

        except Exception as e:
            st.error(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
