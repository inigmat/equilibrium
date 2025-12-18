import datetime
import streamlit as st
from ortools.sat.python import cp_model

from data_loader import load_and_parse_xer, prepare_dataframes
from solver import run_scenario_type_1, run_scenario_type_2
from visualization import plot_gantt_chart, create_excel_download
from solver import DEFAULTUDFLABEL

# Constants
MIMECONST = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# Streamlit page configuration
st.set_page_config(page_title="P6 Scheduler Optimizer", layout="wide")


def main():
    """
    Main function to run the Streamlit web application.
    """
    st.title("📊 Primavera P6 Schedule Optimizer (XER)")
    st.sidebar.header("Settings")

    # File uploader in the sidebar
    uploaded_file = st.sidebar.file_uploader("Upload .xer File", type=["xer"])

    # Project Start Date configuration
    st.sidebar.subheader("Project Start Date")
    use_file_date = st.sidebar.checkbox("Use date from .xer file", value=True)

    project_start = None
    if not use_file_date:
        # Allow user to pick a manual date
        project_start = st.sidebar.date_input(
            "Select project start date",
            value=datetime.date.today()
        )

    # Optimization mode selection
    st.sidebar.subheader("Optimization Settings")  # Изменено название
    mode = st.sidebar.radio(
        "Select Operation Scenario:",
        ("Type 1: Auto-Assignment Optimization",
         "Type 2: Existing Resource Check")
    )

    # Initialize variables for both modes
    nb_workers = 3
    udf_name = DEFAULTUDFLABEL
    subcrew_config = {}

    # Logic for different optimization scenarios
    if mode == "Type 1: Auto-Assignment Optimization":
        nb_workers = st.sidebar.slider("Number of Workers/Crews", 1, 10, 3)
        st.sidebar.info(
            "The algorithm distributes tasks among the specified "
            "number of workers to minimize project duration (Makespan)."
        )
    else:
        udf_name = st.sidebar.text_input(
            "UDF Field Name for Crew/Resource",
            value=DEFAULTUDFLABEL
        )
        st.sidebar.info(
            "The algorithm minimizes Makespan by respecting existing "
            "assignments in the UDF field and optimizes tasks "
            "among configured sub-crews."
        )

    # Process file if uploaded
    if uploaded_file:
        try:
            # 1. Read and Parse the XER file
            xer, project = load_and_parse_xer(uploaded_file)
            tasks_df, rels_df, mile_mask = prepare_dataframes(project)

            # Determine the project start date
            if use_file_date:
                project_start = project.plan_start_date.date()

            st.sidebar.info(f"Project start date: **{project_start}**")

            unique_resources = []
            if mode == "Type 2: Existing Resource Check":
                # Find the UDF type definition by label
                res_udf = next(
                    (el for el in xer.udf_types.values()
                     if el.label == udf_name), None
                )

                if res_udf:
                    assigned_resources = set()
                    for task in project.tasks:
                        if res_udf in task.user_defined_fields:
                            resource = task.user_defined_fields[res_udf]
                            if resource:
                                assigned_resources.add(resource)
                    unique_resources = sorted(list(assigned_resources))

                if unique_resources:
                    st.sidebar.subheader("Sub-Crew Configuration")
                    st.sidebar.caption(
                        "Specify number of sub-crews for each resource "
                        "(1 means no division)."
                    )

                    subcrew_config = {}
                    for resource in unique_resources:
                        subcrew_config[resource] = st.sidebar.slider(
                            f"Sub-Crews for **{resource}**:",
                            1, 10, 1, key=f"subcrew_{resource}"
                        )
                else:
                    subcrew_config = {}

            # Project overview section
            st.subheader("Project Data Overview")
            st.write(
                f"**Project Start:** {project_start.strftime('%Y-%m-%d')} | "
                f"**Total Tasks:** {len(tasks_df)}"
            )

            with st.expander("Preview Raw Task Data"):
                st.dataframe(tasks_df[['task_code', 'task_name', 'duration',
                                       'task_type']].head())

            # 2. Run Optimization Calculation
            if st.button("Run Optimization"):
                with st.spinner("Optimizing schedule..."):
                    if mode == "Type 1: Auto-Assignment Optimization":
                        status, makespan, res_df = run_scenario_type_1(
                            tasks_df, rels_df, mile_mask, nb_workers
                        )
                    else:
                        status, makespan, res_df = run_scenario_type_2(
                            tasks_df, rels_df, xer, project,
                            udf_name, subcrew_config
                        )

                    # 3. Results Display
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

                        # Create tabs for different output views
                        tab1, tab2, tab3 = st.tabs([
                            "Gantt Chart",
                            "Schedule Table",
                            "Excel Download"
                        ])

                        with tab1:
                            # Visualization of the schedule
                            fig = plot_gantt_chart(res_df, tasks_df)
                            if fig:
                                st.pyplot(fig)
                            else:
                                st.warning("No operational tasks found.")

                        with tab2:
                            # Table view of the optimized schedule
                            display_df = res_df.merge(
                                tasks_df[['task_id', 'task_code', 'task_name']],
                                on='task_id'
                            )

                            if mode == "Type 2: Existing Resource Check":
                                display_df.rename(
                                    columns={'resource': 'Resource/Sub-Crew'},
                                    inplace=True
                                )

                            res_col = (
                                'Resource/Sub-Crew'
                                if mode == "Type 2: Existing Resource Check"
                                else 'resource'
                            )

                            st.dataframe(
                                display_df[[
                                    'task_code', 'task_name', res_col,
                                    'start_day', 'end_day'
                                ]]
                            )

                        with tab3:
                            # Prepare and provide Excel download
                            excel_data = create_excel_download(
                                res_df, tasks_df, project_start
                            )
                            st.download_button(
                                label="📥 Download Optimized Schedule",
                                data=excel_data,
                                file_name="optimized_schedule.xlsx",
                                mime=MIMECONST
                            )
                    else:
                        st.error("No optimal solution found.")

        except Exception as e:
            st.error(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
