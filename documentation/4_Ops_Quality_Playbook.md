# Ops & Quality Playbook: [Project Name]

**Owner:** [Lead Engineer]  
**Version:** 1.0  
**Critical Links:** [Link to Monitoring Dashboard] | [Link to Deployment Pipeline]

---

## 1. Quality Assurance (The Test Plan)
This section defines how we prove the software works.

### 1.1 Testing Levels
* **Unit Testing:** Every core function in the `/logic` folder must have a corresponding test. We aim for **80% code coverage**.
* **Integration Testing:** We test the connection between the API and the Database weekly.

### 1.2 Manual Smoke Test
A human must perform these 5 checks before any production release:
* [ ] Can a new user sign up?
* [ ] Does the primary search/action function return results?
* [ ] Can a user log out and log back in?
* [ ] Do the images/assets load correctly?
* [ ] Does the "Contact Support" button work?

---

## 2. Definition of Done (DoD)
A feature is not "Done" just because the code is written. It must pass this checklist:
* [ ] **Code Quality:** Peer review (Pull Request) approved by at least one other dev.
* [ ] **Testing:** All automated tests pass in the CI/CD pipeline.
* [ ] **Documentation:** Doc #2 (Tech Spec) updated with any new API endpoints or DB changes.
* [ ] **Tracking:** The ticket in Doc #3 (Execution Hub) is moved to "Done."
* [ ] **UAT:** The Product Manager has verified the feature on the Staging environment.

---

## 3. Release & Deployment Manual
How we move code from a developer's laptop to the real world.

### 3.1 Environments
* **Development:** Local machines.
* **Staging:** A mirror of production for final testing.
* **Production:** The live site used by customers.

### 3.2 Deployment Process
1.  Merge code into the `main` branch.
2.  Wait for `GitHub Actions / CI/CD` to run automated builds.
3.  Deploy to `Staging` and verify.
4.  Trigger manual "Promote to Production" via [Tool Name, e.g., Vercel/AWS].

---

## 4. Incident Response (The "Fire Drill")
What to do if the site goes down.

### 4.1 Severity Levels
* **Level 1 (Critical):** Site is down or payments are failing. **Action:** Immediate fix required.
* **Level 2 (High):** A major feature is broken for all users. **Action:** Fix within 4 hours.
* **Level 3 (Low):** Minor UI bug or typo. **Action:** Add to next sprint.

### 4.2 Rollback Procedure
If a deployment causes a Level 1 incident:
1.  **Stop:** Do not try to "fix forward" (write more code).
2.  **Revert:** Go to the Deployment Tool and select the previous "Success" build.
3.  **Deploy:** Push the old version live immediately.
4.  **Investigate:** Debug the issue in the Development environment only **after** the site is back up.

---

## 5. Maintenance & Support
* **Logs:** Access system logs at [Link to Log Tool].
* **Database Backups:** Automated backups occur every 24 hours at 2:00 AM UTC.
* **On-Call Rotation:** [Name] (Primary), [Name] (Secondary).
