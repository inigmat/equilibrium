## Equilibrium - P6 Scheduler Optimizer

This project provides a constrained optimization framework for scheduling Primavera P6 projects (XER format) using Google's Constraint Programming Solver (CP-SAT). The primary objective is to minimize project makespan while adhering to complex precedence logic and resource capacity constraints.

### I. Core Functionality

The application implements two distinct optimization models:

  * **Scenario 1: Auto-Assignment Optimization**
      * **Objective:** Global makespan minimization.
      * **Logic:** Dynamically allocates all operational tasks across a pool of $N$ interchangeable resources.
  * **Scenario 2: Existing Resource Check with Sub-Crews**
      * **Objective:** Localized optimization within assigned resource groups.
      * **Logic:** Tasks are grouped by a User Defined Field (UDF). Each primary resource group is partitioned into $N$ parallel **Sub-Crews**, enabling concurrent execution of tasks previously restricted to a single resource thread.

### II. Technical Stack and Dependencies

The system is built using the following environment:

| Component | Role | Libraries Used |
| :--- | :--- | :--- |
| **Solver** | Constraint Programming Engine | `ortools` (CP-SAT Engine) |
| **Interface**| Web Application & I/O | `streamlit` |
| **Data Parsing**| XER File Ingestion | `xerparser` |
| **Data Core**| Data Transformation & Manipulation | `pandas` |
| **Graphics**| Gantt Chart Generation | `matplotlib` |

**Required Package Versions:**

```text
streamlit==1.52.1
pandas==2.3.3
matplotlib==3.10.7
ortools==9.14.6206
xerparser==0.13.8
openpyxl==3.1.5
```

### III. Installation and Deployment

1.  **Clone Repository:**
    ```bash
    git clone https://github.com/inigmat/equilibrium.git
    cd equilibrium
    ```
2.  **Install Dependencies:**
    ```bash
    pip install streamlit==1.28.0 pandas==2.0.3 matplotlib==3.7.2 ortools==9.7.0 xerparser==1.5.1 openpyxl==3.1.2
    ```
3.  **Execute Application:**
    ```bash
    streamlit run app.py
    ```

### IV. Status and Limitations (Demo Mode)

** CURRENT VERSION IS A DEMO/TEST BUILD.**

This version is intended for demonstration purposes and contains known simplifications. Key limitations include:

  * **Calendar Simplification:** The model **does not** incorporate complex P6 calendars. All scheduling calculations (task start/end dates) are performed based on a **continuous 7-day working calendar**. Weekends, holidays, and non-work periods defined in the source XER calendars are currently ignored.
  * **Potential Errors:** Since this is a test environment, various edge case errors or unexpected behavior may occur.

### V. Technical Analysis of Constraint Issues

#### 1\. Infeasibility at N=2 (Scenario 2)

In certain project structures, the model may return an `INFEASIBLE` status specifically when $N=2$, while succeeding at $N=1$ or $N \ge 3$. This is typically a result of **Precedence-Capacity Conflict**:

  * The tight coupling of critical precedence dependencies (e.g., FS links) with the rigid capacity constraint of exactly two parallel Sub-Crews may eliminate the feasible solution space required to minimize Makespan.

#### 2\. Resource Naming Logic

To maintain data integrity between the solver and the visualization layer:

  * **Operational Tasks:** Are explicitly appended with a `- Sub N` suffix (even if $N=1$) to ensure clear resource allocation in Gantt charts.
  * **Milestones:** Retain their original base resource name but are explicitly excluded from Gantt resource-loading tracks, as they consume zero time and zero resource capacity.

### VI. Data Output

The system generates a time-scaled resource-loaded schedule. Results are available for export via `.xlsx` format, where dates are calculated relative to the `plan_start_date` identified in the XER project header.
