# Chauhan Man Power Supply — Attendance & Site Safety

A runnable Flask and SQLite attendance app for Chauhan Man Power Supply. Workers check in from a registered industrial site; managers can record attendance locally for someone without a device, see active shifts, and receive dashboard and browser pop-up alerts when a worker's device reports leaving the site before checkout.

## Run it

Requires Python 3.10+.

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
py app.py
```

Open http://localhost:5000. Demo accounts:

| Role | Username | Password |
|---|---|---|
| Manager | `manager` | `manager123` |
| Worker | `worker` | `worker123` |

Active sites: Amba Shakti Udyog Ltd, B R Oil Industries Pvt Ltd, Vrindavan Industries, and Rajawat House Gwalior. Nexaryon Tower and Office are archived and cannot be selected for new attendance. Past records at those locations remain available. Rajawat House Gwalior uses 26.2726962, 78.1364943. The Banmore site pins are starting estimates; verify each entrance and radius before using attendance there.

## What is included

- Role-based manager and worker screens with password hashing.
- Manager employee registration with unique employee ID, assigned work site, age, gender, permanent and communication addresses, experience, job title, a sign-in account, and a JPG/PNG profile photo (up to 4 MB). Photos are normalized and only available through a manager-authenticated route.
- Worker GPS check-in enforced server-side against the selected site's radius, and GPS checkout.
- Phone passkey verification at check-in and check-out. Employees enroll on their own phone after receiving their account; the phone's platform authenticator can use face unlock, fingerprint, or the device PIN according to its settings. Biometric templates stay on the device and are never uploaded.
- Optional, consent-based 1:1 face comparison between an employee's registered photo and a check-in/check-out camera frame. The camera frame is processed in memory and discarded; face comparison remains paired with the phone authenticator. Employees can disable it and use the existing phone or manager-assisted attendance paths.
- Continuous browser location watch plus periodic location refresh while the worker page remains open during an open shift. A worker pop-up and manager dashboard/browser alert are triggered on a reported geofence exit; alerts can be acknowledged.
- Manager-entered local attendance, accepted only when the manager's device is inside the site's geofence, recorded as `manager_local` and linked to the manager account.
- Manager forms for creating worker accounts and work sites.
- SQLite storage in `attendance.db` (created on first run).

## Advanced workforce features

1. Filter the live roster by employee name or ID.
2. Filter the roster by active workplace.
3. Filter the roster by on-site or needs-attention status.
4. View daily and personal attendance history.
5. Export attendance within a date range as CSV.
6. Assign dated shifts with a start time, end time, and workplace.
7. Flag arrivals more than ten minutes after the scheduled start.
8. Track shift elapsed time in the live roster.
9. Start and finish break periods.
10. Subtract breaks from worked time.
11. Estimate overtime after eight net hours.
12. Submit employee leave requests.
13. Approve or decline leave with a manager note.
14. Submit attendance correction requests with a reason.
15. Review and approve or decline correction requests.
16. Publish team announcements.
17. Let workers acknowledge announcements.
18. Send a manager SOS alert during a shift.
19. Set site capacity, view live occupancy, and block check-in when full.
20. Deactivate or reactivate worker accounts and review manager audit history.
21. Review date-range attendance analytics, including total and completed shifts, net hours, late arrivals, and workplace completion rates.

## Advanced operations additions

1. Create recurring weekly assignments for up to 12 weeks.
2. Block schedule creation when it overlaps approved leave.
3. Block duplicate assignments and scheduling over an active shift.
4. Forecast scheduled staffing by active site for the next seven days.
5. Flag active shifts when net worked time reaches eight hours.
6. Flag active shifts with no GPS ping or a ping older than 15 minutes.
7. Track active employee read receipts for manager announcements.
8. Record and update a shift handover note for the manager.
9. Submit safety incidents with category, severity, and details during an active shift.
10. Triage incidents as open, reviewing, or resolved, with a manager response.
11. Request future shift transfers to an eligible coworker.
12. Approve transfers only after checking the coworker’s schedule, leave, and active shift; approved transfers reassign the shift.
13. Export the employee directory as a manager-only CSV without account passwords.

## Compliance and field-safety additions

14. Maintain employee credentials with issuer, reference, issue date, expiry date, and notes.
15. Flag credentials that expire within 60 days.
16. Distinguish expired, expiring, current, and non-expiring credentials in the manager registry.
17. Let employees review their own recorded credentials and expiry dates.
18. Export credential and compliance status reports as manager-only CSV.
19. Collect four on-site safety checks during an active shift: PPE, hazards, equipment, and emergency readiness.
20. Record one safety checklist per attendance shift and show its result in worker history.
21. Require observations when an inspection item fails.
22. Automatically escalate failed inspections into the manager's safety incident queue, with critical severity for emergency-readiness failures.
23. Show daily inspection completion and failure metrics in the manager compliance dashboard.
24. Review the latest site inspections, outcomes, observations, and submission times.

## Shift readiness and governance additions

25. Save reusable site-and-time shift templates.
26. Fill assignment site and hours by selecting a saved template.
27. Let workers submit recurring weekday availability windows.
28. Show worker-submitted availability to managers.
29. Block assignments outside declared weekdays or hours.
30. Require worker confirmation for upcoming shifts and show response status to both roles.
31. Let workers decline shifts with a response note.
32. Let managers request confirmation again for pending or declined shifts.
33. Detect no-shows after the scheduled start plus a ten-minute grace period.
34. Count unscheduled check-ins and show daily schedule reliability metrics.
35. Alert managers when an active break has lasted at least 60 minutes.
36. Check availability and conflicts when approving a shift transfer.

## Smart staffing algorithm additions

37. Rank eligible workers for a site, date, and shift using a bounded, explainable 0–100 fit score.
38. Exclude workers already assigned that day or currently on an active shift.
39. Exclude workers on approved leave for the requested work date.
40. Enforce submitted weekday and time availability as an eligibility gate.
41. Filter by a required credential that remains valid through the shift date.
42. Add site assignment and up-to-90-day site experience to the match score.
43. Score punctuality against the existing ten-minute schedule grace period.
44. Score historical attendance against assigned shifts over the previous 90 days.
45. Balance assignment load using shifts in the prior 28 days.
46. Reduce fit for recent net hours beyond a 40-hour rolling seven-day baseline.
47. Reduce fit for no-shows during the previous 90 days.
48. Show the score reasons and allow managers to assign a ranked worker directly; the regular schedule validation runs again at submission.

## Demand forecasting and service-level additions

49. Forecast each active site and weekday for the next seven days from the previous eight same-weekday schedule counts.
50. Weight more recent observations more heavily so changed demand has greater influence.
51. Blend the weighted mean with a median to dampen one-off staffing spikes.
52. Show how many of the eight historical weeks had scheduled demand and label forecast confidence.
53. Let managers set a minimum workforce target for each site and weekday.
54. Apply the higher of learned demand and the manager's minimum as the staffing target.
55. Compare forecast demand with scheduled workers and calculate uncovered staffing gaps.
56. Identify days with scheduled staffing above forecast demand.
57. Flag schedules that exceed the workplace's configured capacity.
58. Summarize understaffed days, capacity risks, and low-history forecasts in dashboard KPIs.
59. Open a staffing recommendation for a specific shortage directly from the forecast row.
60. Export all seven-day site forecasts, confidence, staffing gaps, surplus, and capacity risks to CSV.

## Optimized coverage planning additions

61. Configure a standard start and end time for each site/weekday staffing target.
62. Add a credential requirement to a recurring site/weekday coverage target.
63. Generate one multi-site assignment preview from all seven-day forecast gaps.
64. Rank each open slot with the existing explainable worker-fit model.
65. Keep each worker to at most one proposed assignment per day across all sites.
66. Carry forward already scheduled forecast-week hours while building the preview.
67. Apply a 48-hour planning ceiling across existing and proposed shifts.
68. Dynamically penalize forecast-week hours and proposed load to spread work fairly.
69. Stop adding assignments when a site reaches configured capacity.
70. Respect worker availability, approved leave, duplicate shifts, credentials, and active-shift eligibility rules.
71. Show each proposed worker's fit reasons, adjusted score, standard shift window, and projected hours.
72. Explain leftover slots as capacity-limited or having no eligible worker within planner constraints.
73. Cap a preview at 100 assignments to keep large demand spikes reviewable.
74. Recompute eligibility under a write lock before applying a plan, then save the batch atomically.
75. Write one audit entry for each applied optimized coverage batch.

## Shift readiness intelligence additions

76. Assess upcoming, unstarted shifts with an explainable 0–100 operational risk score.
77. Raise risk for declined shifts and unconfirmed shifts, with extra weight when start time is within 24 hours.
78. Check a site/weekday's required credential against worker credentials valid on the scheduled date.
79. Measure rest time from the worker's previous scheduled shift and flag turnarounds below 11 hours.
80. Sum scheduled shift duration within the calendar week and review workloads above 48 hours.
81. Detect runs of six or more consecutive scheduled days.
82. Calculate a rolling 90-day no-show rate from schedules and attendance records.
83. Calculate punctual arrival rates using the established ten-minute grace period.
84. Review failed safety-checklist frequency over the previous 30 days.
85. Combine the signals into Critical, High, Watch, and Ready bands, with per-shift reason explanations.
86. Rank the highest-risk shifts first and group risk counts by site.
87. Link each shift to the smart staffing matcher with its date, site, shift window, and credential requirement prefilled.
88. Show forecast-week hours and consecutive-day totals beside each risk result.
89. Export the full upcoming readiness assessment and its scoring evidence as manager-only CSV.
90. Explain the planner thresholds on the dashboard and label scores as operational heuristics rather than compliance findings.

## Attendance exception intelligence additions

91. Review attendance anomalies from a rolling 30-day window.
92. Build worker-specific shift-duration baselines from the preceding 120 days.
93. Fall back to same-site peer baselines when personal history has fewer than five samples.
94. Require ten site-cohort samples before using the peer fallback.
95. Use median absolute deviation (MAD) and a minimum deviation threshold to limit outlier sensitivity.
96. Detect unusually short or long completed shifts relative to the selected baseline.
97. Compare arrival timing against the scheduled start and the worker's historical arrival pattern.
98. Recalculate distance between recorded GPS coordinates and the site's configured geofence.
99. Flag missing GPS coordinates on GPS-sourced records and distinguish manager-local records by provenance.
100. Surface check-ins outside the configured perimeter and attendance without a matching scheduled shift.
101. Identify shifts left open for more than twelve hours and records with pending correction requests.
102. Combine signals into Critical, High, Review, and Info scores, with reasons visible per record.
103. Sort the review queue by score and show baseline type, sample count, net duration, and site distance.
104. Export the complete manager-only exception queue with the evidence behind each flag.
105. Label all exception scores for human review and avoid treating them as misconduct determinations.

## Site safety trend intelligence additions

106. Normalize recent site safety events by attendance shifts so large sites are compared by exposure.
107. Weight incidents by severity, with open and reviewing incidents carrying an additional unresolved-action factor.
108. Apply a 30-day half-life so recent events influence site risk more than older events.
109. Include failed checklist items as weighted signals and calculate inspection failure rates.
110. Use an overall program baseline as a Bayesian prior with 20 prior shifts to smooth low-volume site estimates.
111. Express the smoothed risk rate as weighted points per 100 attendance shifts.
112. Compare exposure-adjusted risk in the latest 14 days with the preceding 14-day period.
113. Require at least two recent events and a 25% increase before labeling a site trend as rising.
114. Add priority for unresolved critical incidents.
115. Assign Low, Watch, Elevated, and High site-risk bands from the composite score.
116. Label confidence from site attendance exposure (15- and 50-shift thresholds).
117. Surface the most frequent incident category and generate a driver-specific suggested review.
118. Sort sites by risk score, unresolved actions, and name for manager triage.
119. Show inspection failure totals, incident counts, attendance exposure, trends, and recommended review together.
120. Export manager-only site risk scores and their contributing evidence to CSV.

## Handover action management additions

121. Create durable site action items from an active worker shift or directly from the manager dashboard.
122. Categorize actions as safety, equipment, client request, access, coverage, or other.
123. Set low, medium, high, or critical severity with default 72-, 24-, 8-, and 2-hour due times.
124. Allow an explicit due time up to 30 days ahead.
125. Assign site actions to an active worker or leave them visible to the whole site team.
126. Carry unresolved action items across attendance shifts and display them to the next worker at that site.
127. Let workers acknowledge, start, and resolve eligible action items.
128. Let managers assign, reassign, set status, and add a follow-up note.
129. Rank open tasks with a 0–100 priority score using severity, overdue time, item age, and repeated site/category volume.
130. Escalate priority as an action passes its due time and label items due within two hours.
131. Show queue KPIs for open, overdue, critical, urgent, and recently resolved items.
132. Explain each task's priority signals in the manager board.
133. Audit worker and manager action creation, assignment, and status changes.
134. Export the active manager queue with due times, priority evidence, assignment, and notes as CSV.
135. Store actions in a dedicated indexed table linked to sites, users, and the source attendance shift.

The manager live roster also shows when each worker's last location ping arrived. These browser location updates are best-effort and may stop when the phone suspends the page.

## Location and deployment notes

Browser location requires permission and a secure context (HTTPS, except localhost). The worker must keep the app open and location sharing enabled for departure alerts. Mobile browsers can suspend location updates when the tab is backgrounded, the device is offline, or the OS stops the browser. Geofence alerts are therefore best-effort and may be delayed or missed; this browser-only demo is not a tamper-proof timekeeping system. GPS can be inaccurate or spoofed.

WebAuthn phone verification requires a supported browser and secure origin. Local testing must use `http://localhost:5000`; phone access from another device requires HTTPS on a domain configured with `WEBAUTHN_RP_ID` and `WEBAUTHN_ORIGIN`. WebAuthn asks the platform authenticator for user verification and cannot force a camera or face-only method; some devices may use a PIN or fingerprint. The WebAuthn credential does not contain face imagery. Manager-recorded local attendance remains a separate manager-attributed workflow.

The face camera comparison uses OpenCV LBPH on one normalized reference photo and one live frame; `FACE_MATCH_MAX_DISTANCE` controls the starting distance limit (default `55`). This is a prototype threshold, not a validated accuracy guarantee. The comparison does not perform liveness detection, so it can be spoofed with a photograph; the separate phone authenticator remains mandatory. Tune/evaluate thresholds on consented, representative samples before relying on face matching. The linked InsightFace `buffalo_l` model is not used because its supplied weights are restricted to non-commercial research without a separate license.

Before deployment: set a random `SECRET_KEY`, use HTTPS, replace demo credentials/site coordinates, add password reset/account administration, configure backups and retention, and deploy behind a production WSGI server. CSRF protection is enabled for browser form and API writes. For reliable background tracking and push alerts, use a managed mobile app/location service with clear employee notice and consent. Review audit-log access and retention before using these records for payroll decisions.

## Project structure

- `templates/` and `static/`: responsive manager/worker frontend.
- `app.py`: Flask backend, authentication, attendance/geofence rules, API endpoints, and alert handling.
- `schema.sql`: SQLite tables, constraints, relationships, and indexes.
- `DATABASE.md`: architecture and database relationship guide.

Set `SECRET_KEY` as an environment variable before deployment. `Flask-WTF` CSRF protection is included in the dependency list.
