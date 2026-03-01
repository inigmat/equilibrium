## Equilibrium - Schedule Optimizer

This project provides a constrained optimization framework for construction and engineering schedules using Google's Constraint Programming Solver (CP-SAT). It supports both **Primavera P6 XER** and **MS Project** file formats. The primary objective is to minimize project makespan while adhering to complex precedence logic and resource capacity constraints.

### I. Core Functionality

The application implements two distinct optimization models:

  * **Scenario 1: Auto-Assignment Optimization**
      * **Objective:** Global makespan minimization.
      * **Logic:** Dynamically allocates all operational tasks across a pool of $N$ interchangeable resources.
  * **Scenario 2: Existing Resource Check with Sub-Crews**
      * **Objective:** Localized optimization within assigned resource groups.
      * **Logic:** Tasks are grouped by resource assignment (P6 UDF field or MS Project resource assignments). Each primary resource group is partitioned into $N$ parallel **Sub-Crews**, enabling concurrent execution of tasks previously restricted to a single resource thread.

### II. Supported File Formats

| Format | Extension | Source |
| :--- | :--- | :--- |
| Primavera P6 XER | `.xer` | Oracle Primavera P6 |
| MS Project Binary | `.mpp` | Microsoft Project 2003–2024 |
| MS Project XML | `.xml`, `.mspdi` | Microsoft Project (XML export) |
| MS Project MPX | `.mpx` | Legacy MS Project text format |

For MS Project files, resource assignments are read directly from the file and used for Scenario 2 sub-crew configuration. WBS summary and hammock tasks are automatically excluded from the optimization.

### III. Technical Stack and Dependencies

| Component | Role | Libraries Used |
| :--- | :--- | :--- |
| **Solver** | Constraint Programming Engine | `ortools` (CP-SAT) |
| **Interface** | Web Application & I/O | `streamlit` |
| **P6 Parsing** | XER File Ingestion | `xerparser` |
| **MPP Parsing** | MS Project File Ingestion | `mpxj`, `JPype1` |
| **Data Core** | Data Transformation & Manipulation | `pandas` |
| **Graphics** | Gantt Chart Generation | `matplotlib` |

**Required Package Versions:**

```text
streamlit==1.52.2
pandas==2.3.3
matplotlib==3.10.7
ortools==9.14.6206
xerparser==0.13.8
openpyxl==3.1.5
mpxj==15.3.1
```

> **Note:** MS Project file support requires **Java 11 or later** installed on the host machine. JPype1 is installed automatically as a dependency of mpxj. Java is not required for XER files.

### IV. Installation and Deployment

1.  **Install Java 11+ (required for MS Project files):**
    ```bash
    # macOS (Homebrew)
    brew install --cask temurin@21

    # Verify
    java -version
    ```

2.  **Clone Repository:**
    ```bash
    git clone https://github.com/inigmat/equilibrium.git
    cd equilibrium
    ```

3.  **Create virtual environment and install dependencies:**
    ```bash
    python -m venv venv
    source venv/bin/activate      # Windows: venv\Scripts\activate
    pip install -r requirements.txt
    ```

4.  **Execute Application:**
    ```bash
    streamlit run app.py
    ```

### V. Status and Limitations (Demo Mode)

**CURRENT VERSION IS A DEMO/TEST BUILD.**

This version is intended for demonstration purposes and contains known simplifications. Key limitations include:

  * **Calendar Simplification:** The model does not incorporate complex working calendars. All scheduling calculations are performed based on the declared hours-per-day from the file's default calendar, without accounting for weekends, holidays, or non-work periods.
  * **In-Progress Schedules:** Completed and in-progress tasks are pinned to their actual dates. Only not-started tasks are optimized.
  * **MS Project Scenario 2:** Resource grouping is based on the first resource assignment per task. Tasks with no resource assignment are excluded from Scenario 2.
  * **Potential Errors:** Since this is a test environment, various edge case errors or unexpected behavior may occur.

### VI. Data Output

The system generates a time-scaled resource-loaded schedule. Results are available for export via `.xlsx` format, where dates are calculated relative to the project start date identified in the file header.
