# OpenSSF Best Practices Submission Draft (Vaner)

This file captures current evidence and prebuilt automation links for
`github.com/Borgels/vaner` on bestpractices.dev.

Project page: <https://www.bestpractices.dev/en/projects/12597>

## Project metadata

- **Project name:** Vaner
- **Project URL:** `https://github.com/Borgels/vaner`
- **Primary language:** Python
- **License:** Apache-2.0 (`LICENSE`)
- **Security contact:** `security@vaner.ai`
- **Security page:** `https://docs.vaner.ai/security`

## Key evidence locations

- Contribution policy and testing rules: `CONTRIBUTING.md`
- Security and disclosure policy: `SECURITY.md`
- Governance and elevated-permission policy: `GOVERNANCE.md`
- Maintainer and sensitive-resource ownership: `MAINTAINERS.md`
- Support scope and release support model: `SUPPORT.md`
- Threat model: `docs/threat-model.md`
- Security assessment summary: `docs/security-assessment.md`
- CI/release/security automation: `.github/workflows/`

## Confirmed GitHub settings snapshot

- Branch protection on `main` with strict checks and review gates
- Required checks: `verify (ubuntu-latest, 3.12)`, `examples-smoke`, `no-moat-paths`, `actionlint`
- Required PR review settings include code-owner reviews and non-author approvals
- Actions default workflow token permissions: `read`
- Security advisories: enabled
- Secret scanning alerts: enabled
- Secret scanning push protection: enabled
- Dependabot security updates: enabled
- Organization-wide required MFA: currently **not enabled**

## Automation proposal links

### Baseline 1 (proposed Met fields)

<https://www.bestpractices.dev/en/projects/12597/baseline-1/edit?osps_ac_02_01_status=Met&osps_ac_02_01_justification=Organization+default+repository+permission+is+set+to+read%2C+and+elevated+permissions+are+granted+explicitly+as+needed.&osps_ac_03_01_status=Met&osps_ac_03_01_justification=Main+branch+protection+prevents+direct+commits+and+requires+pull+request+review+and+required+checks.&osps_ac_03_02_status=Met&osps_ac_03_02_justification=Main+branch+protection+disallows+branch+deletion.&osps_br_03_01_status=Met&osps_br_03_01_justification=Official+project+channels+are+served+over+encrypted+transport+%28HTTPS%29%2C+including+GitHub+and+docs.vaner.ai.&osps_br_03_02_status=Met&osps_br_03_02_justification=Distribution+channels+use+authenticated+encrypted+transport+and+signed%2Fprovenance-backed+release+workflows.&osps_br_07_01_status=Met&osps_br_07_01_justification=Repository+policy+forbids+committing+secrets%2C+and+GitHub+secret+scanning+with+push+protection+is+enabled.&osps_do_01_01_status=Met&osps_do_01_01_justification=README+and+docs.vaner.ai+provide+user+guidance+for+installation%2C+configuration%2C+and+core+usage.&osps_do_02_01_status=Met&osps_do_02_01_justification=SUPPORT.md+and+issue+templates+document+how+to+report+defects.&osps_gv_02_01_status=Met&osps_gv_02_01_justification=Public+issues+and+discussions+are+enabled+for+usage+and+change+discussions.&osps_gv_03_01_status=Met&osps_gv_03_01_justification=CONTRIBUTING.md+documents+the+contribution+process+and+required+checks.&osps_le_02_01_status=Met&osps_le_02_01_justification=Source+code+is+licensed+under+Apache-2.0.&osps_le_02_02_status=Met&osps_le_02_02_justification=Released+software+assets+are+distributed+under+Apache-2.0+licensing+terms.&osps_le_03_01_status=Met&osps_le_03_01_justification=The+repository+includes+an+Apache-2.0+LICENSE+file+at+the+root.&osps_qa_01_01_status=Met&osps_qa_01_01_justification=The+authoritative+source+repository+is+publicly+readable+at+github.com%2FBorgels%2Fvaner.&osps_qa_01_02_status=Met&osps_qa_01_02_justification=GitHub+commit+history+publicly+records+what+changed%2C+who+changed+it%2C+and+when.&osps_qa_02_01_status=Met&osps_qa_02_01_justification=Direct+dependencies+are+declared+in+pyproject.toml+and+lockfiles+are+maintained+in+requirements%2F%2A.txt.&osps_qa_05_01_status=Met&osps_qa_05_01_justification=Contributor+policy+forbids+committing+generated+executable+artifacts+to+version+control.&osps_qa_05_02_status=Met&osps_qa_05_02_justification=Contributor+policy+forbids+committing+unreviewable+binary+artifacts+to+version+control.&osps_vm_02_01_status=Met&osps_vm_02_01_justification=SECURITY.md+includes+security+contact+information+and+reporting+guidance.>

### Baseline 2 (proposed Met fields)

<https://www.bestpractices.dev/en/projects/12597/baseline-2/edit?osps_ac_04_01_status=Met&osps_ac_04_01_justification=GitHub+Actions+default+workflow+token+permissions+are+configured+to+read-only%2C+and+workflows+elevate+permissions+only+where+required.&osps_br_02_01_status=Met&osps_br_02_01_justification=Releases+use+unique+version+identifiers+via+tags+and+package+versioning.&osps_br_05_01_status=Met&osps_br_05_01_justification=Build+and+release+pipelines+use+standard+ecosystem+tooling+and+hash-locked+dependency+files.&osps_do_06_01_status=Met&osps_do_06_01_justification=CONTRIBUTING.md+documents+dependency+declaration%2C+lockfile+generation%2C+and+update+expectations.&osps_do_07_01_status=Met&osps_do_07_01_justification=CONTRIBUTING.md+documents+how+to+set+up%2C+build%2C+and+test+the+project.&osps_gv_01_01_status=Met&osps_gv_01_01_justification=MAINTAINERS.md+documents+current+maintainers+and+sensitive-resource+ownership.&osps_gv_01_02_status=Met&osps_gv_01_02_justification=GOVERNANCE.md+and+MAINTAINERS.md+document+roles+and+responsibilities.&osps_gv_03_02_status=Met&osps_gv_03_02_justification=CONTRIBUTING.md+and+PR+templates+define+contribution+quality+and+acceptance+requirements.&osps_qa_03_01_status=Met&osps_qa_03_01_justification=Branch+protection+requires+configured+CI+checks+to+pass+before+merge.&osps_qa_06_01_status=Met&osps_qa_06_01_justification=CI+runs+automated+test+suites+on+pull+requests+before+merge+to+main.&osps_sa_01_01_status=Met&osps_sa_01_01_justification=Architecture+and+threat-model+documentation+describe+actors+and+actions+across+the+system.&osps_sa_02_01_status=Met&osps_sa_02_01_justification=CLI+and+integration+docs+describe+external+software+interfaces+and+usage+patterns.&osps_sa_03_01_status=Met&osps_sa_03_01_justification=A+documented+security+assessment+process+and+summary+is+maintained+in+docs%2Fsecurity-assessment.md.&osps_vm_01_01_status=Met&osps_vm_01_01_justification=SECURITY.md+defines+coordinated+vulnerability+disclosure+with+response+targets.&osps_vm_03_01_status=Met&osps_vm_03_01_justification=Private+vulnerability+reporting+is+available+through+security%40vaner.ai.&osps_vm_04_01_status=Met&osps_vm_04_01_justification=Public+vulnerability+publication+is+handled+through+GitHub+Security+Advisories+and+documented+in+SECURITY.md.>

### Baseline 3 (proposed Met fields)

<https://www.bestpractices.dev/en/projects/12597/baseline-3/edit?osps_ac_04_02_status=Met&osps_ac_04_02_justification=Workflows+use+least-privilege+permissions+with+default+read-only+token+access+and+explicit+elevation+only+where+needed.&osps_br_02_02_status=Met&osps_br_02_02_justification=Release+assets+are+published+under+tagged+releases+and+associated+with+the+release+identifier.&osps_br_07_02_status=Met&osps_br_07_02_justification=SECURITY.md+defines+secrets+and+credentials+handling+policy%2C+including+storage%2C+access%2C+and+rotation+expectations.&osps_do_03_01_status=Met&osps_do_03_01_justification=SECURITY.md+documents+commands+to+verify+release+integrity.&osps_do_03_02_status=Met&osps_do_03_02_justification=SECURITY.md+documents+expected+release+author+identity%2Fprocess+verification+via+OIDC%2Fcosign+guidance.&osps_do_04_01_status=Met&osps_do_04_01_justification=SUPPORT.md+and+SECURITY.md+describe+release+support+scope+and+duration+expectations.&osps_do_05_01_status=Met&osps_do_05_01_justification=Support+and+security+policy+documents+describe+when+versions+no+longer+receive+security+updates.&osps_gv_04_01_status=Met&osps_gv_04_01_justification=GOVERNANCE.md+documents+review+and+approval+policy+before+granting+elevated+sensitive-resource+access.&osps_qa_02_02_status=Met&osps_qa_02_02_justification=Release+workflow+generates+and+publishes+an+SBOM+alongside+release+artifacts.&osps_qa_06_02_status=Met&osps_qa_06_02_justification=CONTRIBUTING.md+documents+when+and+how+tests+run+locally+and+in+CI.&osps_qa_06_03_status=Met&osps_qa_06_03_justification=CONTRIBUTING.md+requires+major+behavioral+changes+to+add+or+update+automated+tests.&osps_qa_07_01_status=Met&osps_qa_07_01_justification=Branch+protection+requires+non-author+pull+request+approvals+before+merge.&osps_sa_03_02_status=Met&osps_sa_03_02_justification=Threat+modeling+and+attack-surface+analysis+are+documented+in+docs%2Fthreat-model.md.&osps_vm_05_01_status=Met&osps_vm_05_01_justification=SECURITY.md+defines+SCA+remediation+thresholds+for+vulnerabilities+and+licenses.&osps_vm_05_02_status=Met&osps_vm_05_02_justification=SECURITY.md+defines+policy+to+address+SCA+violations+before+release.&osps_vm_05_03_status=Met&osps_vm_05_03_justification=Changes+are+evaluated+with+SCA+checks+in+CI+and+governed+by+documented+suppression+and+remediation+policy.&osps_vm_06_01_status=Met&osps_vm_06_01_justification=SECURITY.md+defines+SAST+remediation+thresholds.>

## Remaining unresolved fields (suggested)

The following remain unresolved or likely unmet without additional work:

- `osps_ac_01_01`: org-wide mandatory MFA currently not enabled
- `osps_br_01_01`: stronger explicit CI input sanitization evidence needed
- `osps_br_01_03`: stronger explicit CI privilege-isolation evidence needed
- `osps_le_01_01`: DCO policy exists, but full all-commit enforcement evidence may need stronger automation proof
- `osps_vm_04_02`: no VEX feed currently
- `osps_vm_06_02`: SAST policy documented, but explicit merge-blocking evidence may need stronger proof

Helper link for unresolved statuses:

<https://www.bestpractices.dev/en/projects/12597/choose/edit?osps_ac_01_01_status=Unmet&osps_ac_01_01_justification=Organization-wide+mandatory+MFA+is+not+currently+enabled.&osps_br_01_01_status=%3F&osps_br_01_01_justification=Needs+explicit+workflow-level+evidence+for+metadata+sanitization+coverage.&osps_br_01_03_status=%3F&osps_br_01_03_justification=Needs+explicit+evidence+that+untrusted-code+jobs+cannot+access+privileged+CI+assets+in+all+cases.&osps_le_01_01_status=%3F&osps_le_01_01_justification=DCO+is+policy-required%2C+but+explicit+all-commit+enforcement+evidence+still+needs+confirmation.&osps_vm_04_02_status=Unmet&osps_vm_04_02_justification=No+VEX+feed%2Fpublication+process+is+currently+implemented.&osps_vm_06_02_status=%3F&osps_vm_06_02_justification=SAST+policy+is+documented%2C+but+explicit+merge-blocking+enforcement+evidence+is+still+needed.>
