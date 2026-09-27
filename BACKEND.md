# SomaSync — Django Backend

Offline-first learning/assessment platform backend for Tanzanian secondary
schools. Django 6.1 / DRF / PostgreSQL / JWT auth, with AI (Ollama +
Qwen2.5:3b) mediated entirely through a service layer — never exposed
directly to the frontend.

## Core design rules

These are load-bearing invariants, enforced in code and covered by tests:

- **Exactly three roles**: `ADMIN`, `TEACHER`, `STUDENT`. No separate
  system-admin/school-admin roles — an admin's reach is tracked via
  `admin_scope` (`SYSTEM` or `SCHOOL`), a field on the same role.
- **Multi-tenancy via `school`**: every tenant-owned model carries a
  denormalized `school` FK. It is *never* trusted from the client — always
  derived server-side from `request.user.school` (or, for a `SYSTEM` admin
  only, an explicitly supplied value, since they legitimately span every
  school). See `common/mixins.py` (`TenantScopedQuerysetMixin`,
  `TenantOwnedViewSetMixin`) and `common/permissions/`.
- **Deterministic quiz marking, never AI.** `QuizMarkingService`
  (`apps/quizzes/services.py`) is a pure function of the stored answer key
  — same inputs always produce the same score.
- **AI is reserved for two things**: "Explain My Mistake" (a student asks
  why their own wrong answer was wrong) and AI-*assisted* marking of
  subjective school-exam answers — and even there, AI only ever populates
  `ai_marks_awarded`/`ai_feedback`; a human teacher's own explicit action
  is the only thing that ever writes `teacher_final_marks`. Verified live
  in this project with a real case where the AI suggested one score and
  the teacher's own judgment recorded a different one — the published
  result used the teacher's number.
- **Offline-first**: client-generated IDs (`client_attempt_id`,
  `client_answer_id`) make sync idempotent — pushing the same item twice
  never duplicates it. See `apps/sync/`.
- **No TIE/NECTA certification claims** anywhere in the product.

## Apps

| App | Responsibility |
|---|---|
| `accounts` | Custom `User` model (3 roles + admin_scope), JWT login/refresh/logout |
| `schools` | Tenant root (`School`) |
| `academics` | `ClassLevel`, `Subject`, `Topic`, `LearningObjective` |
| `classes` | `SchoolClass`, teacher/student membership, class-code self-join |
| `lessons` | Flat lessons + **mastery-based sections** (see below) |
| `quizzes` | `Quiz`/`Question`/`QuestionOption`, deterministic marking, offline attempt sync |
| `progress` | `LessonProgress` |
| `sync` | Generic offline push/pull engine, dispatches to per-entity handlers |
| `exams` | School exam lifecycle: questions, candidates, script upload, marking (manual + AI-assisted), results |
| `ai` | `AIService` abstraction over Ollama; "Explain My Mistake" |
| `analytics` | Read-only reporting for teacher/school-admin/system-admin tiers |
| `common` | Shared mixins, permissions, pagination, exception handling, throttling, file-checksum helper |

## Phase history (all 22 phases complete)

1. Project foundation — Django/DRF/Postgres/JWT scaffolding, response envelope, exception handling
2. Users, auth, roles — custom `User`, 3-role + admin_scope model, JWT login/refresh
3. Schools & multi-tenancy — tenant boundary, `IsOwnSchool`, `TenantScopedQuerysetMixin`
4. Academic structure — `ClassLevel`/`Subject`/`Topic`/`LearningObjective`
5. Classes & membership — `SchoolClass`, teacher/student membership, class-code join
6. Lesson management — flat lessons, publish workflow, attachments
7. Quiz system — `Quiz`/`Question`/`QuestionOption`/`MatchingPair`, authoring
8. Offline quiz attempts — live start/answer/submit + offline sync, `client_attempt_id` idempotency
9. Deterministic quiz marking — `QuizMarkingService`, MCQ/True-False/Matching, never AI
10. Student progress — `LessonProgress`
11. Sync engine — generic push/pull dispatch, per-entity handlers, batched transaction (hardened in Phase 21)
12. AI service foundation — `AIService` abstraction, Ollama backend, health check, never exposes Ollama directly
13. Explain My Mistake — AI explanation of a student's own wrong answer, cached, failure-tolerant
14. School exam management — `Exam`, `ExamQuestion`, marking scheme, candidates
15. Exam script upload — file metadata only, checksum/extension/size validated
16. Exam marking engine — objective auto-mark, subjective manual mark
17. AI-assisted subjective marking — AI suggests, teacher's own action finalizes; AI never finalizes
18. Results & publishing — finalize → publish gate, student visibility only after publish
19. Analytics — 12 endpoints across teacher/school-admin/system-admin tiers; found and fixed a real N+1
20. Security hardening — rate limiting (login + AI), token-blacklist logout, weak-`SECRET_KEY` boot guard, AI prompt-size bound, chunked file-checksum consistency
21. Performance / low-end optimization — batched sync transactions, `CONN_MAX_AGE`, GZip compression, targeted DB indexes
22. Full testing — coverage baseline (90%), closed the untested production-settings-guard gap, one capstone end-to-end lifecycle test

## Mastery-based lesson sections (added after Phase 22)

A lesson can now be broken into sequential, **gated** sections instead of
(or alongside) its flat `content`/attachments — fully backward compatible;
a lesson with zero sections behaves exactly as before.

- `LessonSection` → `SectionContentBlock` (heading/text/image/key_point;
  images reuse the existing `LessonAttachment` upload path, no duplicate
  file-handling logic) → `SectionMiniQuiz`.
- **`SectionMiniQuiz` wraps the existing `Quiz`/`Question`/`QuestionOption`
  system** rather than a parallel flat-schema quiz model — deterministic
  marking (`QuizMarkingService`) and MCQ/True-False/Matching support come
  for free.
- Gating: section 1 is always available; section *N* unlocks only once
  section *N-1*'s mini-quiz is passed (`passing_score`, default 70%,
  teacher-configurable). `StudentSectionProgress` tracks status
  (LOCKED/AVAILABLE/COMPLETED), best score, and attempt count.
- New endpoints: `GET /api/lessons/{id}/structure/` (per-student section
  status), and under `/api/lessons/sections/{id}/`: `content-blocks/`,
  `mini-quiz/` (authoring), `content/` (student-facing, answer-hidden,
  403 if locked), `submit-quiz/` (deterministic marking, unlocks the next
  section on pass, returns per-question explanations on failure).
- Side effect: added `Question.explanation` (optional, teacher-authored)
  and `SafeQuestionSerializer`/`SafeQuestionOptionSerializer` to
  `apps/quizzes` — closing a previously-noted-but-never-built gap (a
  student-safe, answer-hidden question view).

## Fixes made in this session

- **`createsuperuser` was broken.** Django's default manager doesn't know
  about the custom `role`/`admin_scope`/`school` fields, so it left
  `role=''`, violating the `school_required_unless_system_admin` DB check
  constraint. Fixed with a custom `UserManager` that makes a Django
  superuser automatically a `role=ADMIN, admin_scope=SYSTEM` system admin
  (`apps/accounts/models.py`).

## Testing

- **519 tests**, all passing (`python manage.py test config apps.accounts
  apps.schools apps.academics apps.classes apps.lessons apps.quizzes
  apps.progress apps.sync apps.ai apps.exams apps.analytics`).
- **90% line coverage** (`coverage run --source='apps,common,config'
  manage.py test ... && coverage report`).
- One capstone end-to-end test (`config/tests_e2e.py`) walks the entire
  lesson → quiz → offline sync → AI explanation → exam →
  AI-assisted-marking-with-teacher-review → publish → analytics lifecycle
  in one test.
- Every phase was also verified live against a real running dev server
  (including genuine, non-mocked Ollama calls), not just the automated
  suite.

## Known gaps / suggested next steps

1. **No API to create teacher/student/school-admin accounts.** They only
   exist via Django admin or direct ORM — no self-serve provisioning
   endpoint for a school admin.
2. **No password change or reset flow.** `GET /api/auth/me/` is read-only;
   there's no "change my password" or "forgot password" endpoint, and
   `EMAIL_BACKEND` is console-only (dev-only, not real email).
3. **No generated API docs** (OpenAPI/Swagger) for whoever builds the
   frontend.
4. **Not yet addressed (ops, not code bugs)**: containerization, CI
   pipeline, production static-file serving, error monitoring, DB backup
   strategy.
5. **Frontend (React/Vite/Tailwind/Dexie) has not been started.**

## Running locally

```bash
source soma_venv/bin/activate
python manage.py migrate
python manage.py createsuperuser   # now works correctly — creates a SYSTEM admin
python manage.py runserver 127.0.0.1:8000
```

Settings module is selected via `DJANGO_SETTINGS_MODULE` in `.env`
(`config.settings.development` locally; `config.settings.production`
refuses to boot without a strong `SECRET_KEY` and explicit `ALLOWED_HOSTS`).
