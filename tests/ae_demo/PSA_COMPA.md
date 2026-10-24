# Test case: Application Engine that fails on real HCM data (PSA_COMPA)

Purpose: show the troubleshooter proving a **data/setup issue** with read-only
queries (the AEMINITEST case is a **code issue** it solves from the code alone).

**PSA_COMPA** computes every active employee's compa-ratio, company by company:

    compa-ratio = annual pay (PS_JOB.ANNUAL_RT) / grade midpoint (PS_SAL_GRADE_TBL.MID_RT_ANNUAL)

On HR92U052 (checked read-only on 2026-10-24) company **S13** has 27 of its 29
graded active employees in SetID **SD001** salary grades (plans 0003, 0008, 0014;
grades A5-A9, E6-E13) whose annual midpoint is **0**. All other companies are
fine. The run processes companies in order and fails on S13 with
`ORA-01476: divisor is equal to zero`; the error itself does not say which
company, grade or employees are involved.

## 1. Records (App Designer: File > New > Record, then Build > Create Tables)

Existing fields only.

| Record | Type | Fields (keys first) |
|---|---|---|
| `PSA_COMPA_OUT` | SQL Table | **PROCESS_INSTANCE**, **EMPLID**, **EMPL_RCD** (keys), COMPANY, GRADE, ANNUAL_RT, RATE |
| `PSA_COMPA_AET` | SQL Table (state record) | **PROCESS_INSTANCE** (key), COMPANY, HEADCOUNT |

## 2. Application Engine `PSA_COMPA`

Definition Properties: description *Compa-ratio by company*; State Records tab:
`PSA_COMPA_AET` (default); Advanced tab: **Disable Restart**.

**Section MAIN**

- Step01 *Clear earlier output*, action **SQL**:

  ```sql
  DELETE FROM PS_PSA_COMPA_OUT WHERE PROCESS_INSTANCE = %Bind(PROCESS_INSTANCE)
  ```

- Step02 *Each company*, action **Call Section** `COMPANY`

**Section COMPANY**, Step01 *Per company*, three actions in this order:

1. **Do Select** (Select/Fetch):

   ```sql
   %Select(COMPANY)
   SELECT DISTINCT COMPANY FROM PS_JOB WHERE HR_STATUS = 'A' ORDER BY COMPANY
   ```

2. **PeopleCode** (one progress line per company in the message log):

   ```
   SQLExec("SELECT COUNT(*) FROM PS_JOB WHERE COMPANY = :1 AND HR_STATUS = 'A'", PSA_COMPA_AET.COMPANY, PSA_COMPA_AET.HEADCOUNT);
   MessageBox(0, "", 0, 0, "Compa-ratio: company " | PSA_COMPA_AET.COMPANY | ", " | PSA_COMPA_AET.HEADCOUNT | " active job rows");
   ```

3. **Call Section** `CALC`

**Section CALC**, Step01 *Calculate compa-ratio*, action **SQL** (fails here):

```sql
INSERT INTO PS_PSA_COMPA_OUT (PROCESS_INSTANCE, EMPLID, EMPL_RCD, COMPANY, GRADE, ANNUAL_RT, RATE)
SELECT %Bind(PROCESS_INSTANCE), J.EMPLID, J.EMPL_RCD, J.COMPANY, J.GRADE, J.ANNUAL_RT,
       ROUND(J.ANNUAL_RT / G.MID_RT_ANNUAL, 2)
  FROM PS_JOB J, PS_SAL_GRADE_TBL G
 WHERE J.COMPANY = %Bind(COMPANY)
   AND J.HR_STATUS = 'A'
   AND J.EFFDT = (SELECT MAX(J1.EFFDT) FROM PS_JOB J1
                   WHERE J1.EMPLID = J.EMPLID AND J1.EMPL_RCD = J.EMPL_RCD AND J1.EFFDT <= %CurrentDateIn)
   AND J.EFFSEQ = (SELECT MAX(J2.EFFSEQ) FROM PS_JOB J2
                    WHERE J2.EMPLID = J.EMPLID AND J2.EMPL_RCD = J.EMPL_RCD AND J2.EFFDT = J.EFFDT)
   AND G.SETID = J.SETID_SALARY AND G.SAL_ADMIN_PLAN = J.SAL_ADMIN_PLAN AND G.GRADE = J.GRADE
   AND G.EFFDT = (SELECT MAX(G1.EFFDT) FROM PS_SAL_GRADE_TBL G1
                   WHERE G1.SETID = G.SETID AND G1.SAL_ADMIN_PLAN = G.SAL_ADMIN_PLAN
                     AND G1.GRADE = G.GRADE AND G1.EFFDT <= %CurrentDateIn)
```

## 3. Process definition (browser)

PeopleTools > Process Scheduler > Processes > Add a New Value: type
**Application Engine**, name **PSA_COMPA**, description *Compa-ratio by company*.
Process Definition Options tab: Component **AE_REQUEST**, Process Group
**TLSALL** (same as AEMINITEST). Save.

## 4. Run

Chat: *"Run the AE program PSA_COMPA"* (choose server **PRCS8622** on the
pop-up; requests without a server stay Queued on this system), or Request AE
by hand.

Expected: **No Success**. Message log: one line per company (ACC ... ), the last
one *"Compa-ratio: company S13, ..."*, then the SQL error ORA-01476 in
**PSA_COMPA.CALC.Step01**.

## 5. Troubleshoot

Chat: *"Process <instance> failed. What's wrong?"*

Expected diagnosis (category **data** or **setup**):

- failing place: PSA_COMPA.CALC.Step01 (SQL), while company S13 was processed;
- cause: `ANNUAL_RT / G.MID_RT_ANNUAL` with midpoint 0 for SetID SD001 grades;
- evidence: the last message-log line (S13), the SQL, and a read-only query
  listing the zero-midpoint grades and the 27 affected S13 employees;
- fix: enter the midpoints (Set Up HCM > Product Related > Compensation >
  Base Compensation > Salary Plan / Grade setup), or harden the code to skip
  or report grades without a midpoint, then rerun.

Pay (ANNUAL_RT) is masked in the agent's query results; midpoints are not.

Check the setup before the demo (read-only):

```sql
SELECT J.SAL_ADMIN_PLAN, J.GRADE, COUNT(*)
  FROM PS_JOB J, PS_SAL_GRADE_TBL G
 WHERE J.COMPANY = 'S13' AND J.HR_STATUS = 'A'
   AND J.EFFDT = (SELECT MAX(EFFDT) FROM PS_JOB WHERE EMPLID = J.EMPLID AND EMPL_RCD = J.EMPL_RCD AND EFFDT <= SYSDATE)
   AND J.EFFSEQ = (SELECT MAX(EFFSEQ) FROM PS_JOB WHERE EMPLID = J.EMPLID AND EMPL_RCD = J.EMPL_RCD AND EFFDT = J.EFFDT)
   AND G.SETID = J.SETID_SALARY AND G.SAL_ADMIN_PLAN = J.SAL_ADMIN_PLAN AND G.GRADE = J.GRADE
   AND G.EFFDT = (SELECT MAX(EFFDT) FROM PS_SAL_GRADE_TBL WHERE SETID = G.SETID AND SAL_ADMIN_PLAN = G.SAL_ADMIN_PLAN
                   AND GRADE = G.GRADE AND EFFDT <= SYSDATE)
   AND NVL(G.MID_RT_ANNUAL, 0) = 0
 GROUP BY J.SAL_ADMIN_PLAN, J.GRADE;      -- 12 grades, 27 employees on 2026-10-24
```
