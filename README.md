## Equilibrium - Scheduler Optimizer

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

  * **Python 3.8+**
  * **Solver:** `ortools` (CP-SAT Engine)
  * **Frontend:** `streamlit`
  * **Parsing:** `xerparser`
  * **Data Core:** `pandas`
  * **Graphics:** `matplotlib`

**Required Package Versions:**

```text
streamlit==1.28.0
pandas==2.0.3
matplotlib==3.7.2
ortools==9.7.0
xerparser==1.5.1
openpyxl==3.1.2
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

### IV. Technical Analysis of Constraint Issues

#### 1\. Infeasibility at N=2 (Scenario 2)

In certain project structures, the model may return an `INFEASIBLE` status specifically when $N=2$, while succeeding at $N=1$ or $N \ge 3$. This is typically a result of **Precedence-Capacity Conflict**:

  * At $N=1$, tasks are purely sequential.
  * At $N=2$, the solver attempts to parallelize two critical paths. If these paths share rigid dependencies (e.g., a Start-to-Finish link with zero lag) that cannot be satisfied within the mathematical bounds of exactly two parallel tracks, the state space becomes empty.
  * At $N \ge 3$, the additional degree of freedom allows the solver to bypass these rigid intersections.

#### 2\. Resource Naming Logic

To maintain data integrity between the solver and the visualization layer:

  * **Operational Tasks:** Are explicitly appended with a `- Sub N` suffix (even if $N=1$) to ensure clear resource allocation in Gantt charts.
  * **Milestones:** Retain their original base resource name but are excluded from Gantt resource-loading tracks to prevent visual artifacts, as they consume zero time and zero resource capacity.

### V. Data Output

The system generates a time-scaled resource-loaded schedule. Results are available for export via `.xlsx` format, where dates are calculated relative to the `plan_start_date` identified in the XER project header.
