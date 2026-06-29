# TC-UV-5 Exemption — Accessibility Scan

**Run:** run-qa-feature-workabee-dev-002  
**Date:** 2026-06-29  
**Reason:** axe-core/playwright is not available in headless `claude -p` subprocess. No `@axe-core/playwright` package installed in this environment.  
**Pages visited:** /dashboard, /reservation/BK_J755DICT, /reservation/BK_GZHLE6YJ  
**Action:** Manual accessibility review deferred. No structural changes to DOM layout were introduced by INV-540 (quantity is a data field, not a UI component change).  
**Status:** incomplete — not a regression risk for this ticket scope.
