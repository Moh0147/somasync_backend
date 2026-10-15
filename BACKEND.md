# SomaSync Backend — Complete Reference

Django 6.1 REST API for SomaSync, an offline-first PWA for Tanzanian secondary schools.

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Framework | Django 6.1 + Django REST Framework |
| Database | PostgreSQL |
| Authentication | JWT via `djangorestframework-simplejwt` |
| AI (cloud) | OpenAI GPT-4o / GPT-4o Vision |
| AI (local/offline) | Ollama (`qwen2.5:3b` text, `gemma3:4b` vision) |
| File storage | Local disk (dev), swappable to object storage |
| Config | `python-decouple` (`.env` file) |

---

## Project Structure

```
somasync_backend/
├── config/
│   ├── settings/
│   │   ├── base.py          # Shared settings
│   │   ├── development.py   # Dev overrides (throttling disabled)
│   │   └── production.py
│   ├── urls.py              # Root URL config
│   └── wsgi.py
└── apps/
    ├── accounts/    # Auth, users, roles
    ├── schools/     # School management
    ├── academics/   # Class levels, subjects, topics, objectives
    ├── classes/     # School classes, teacher/student membership
    ├── lessons/     # Lessons, sections, content blocks, mini-quizzes
    ├── quizzes/     # Quizzes, questions, student attempts
    ├── progress/    # Lesson progress tracking
    ├── exams/       # Exam scripts, marking, OCR, AI marking
    ├── ai/          # AI explanation requests, cache
    ├── analytics/   # Teacher, school, system analytics
    └── sync/        # Offline sync push/pull
```

---

## Configuration (`.env`)

```env
SECRET_KEY=
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DB_NAME=somasync
DB_USER=somasync
DB_PASSWORD=
DB_HOST=localhost
DB_PORT=5432
CORS_ALLOWED_ORIGINS=https://localhost:5173
JWT_ACCESS_MINUTES=30
JWT_REFRESH_DAYS=7
OPENAI_API_KEY=          # Optional — falls back to Ollama if empty
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:3b
OLLAMA_VISION_MODEL=gemma3:4b
OLLAMA_TIMEOUT_SECONDS=90
```

---

## Authentication

- **JWT Bearer tokens** — `Authorization: Bearer <access_token>`
- Access token lifetime: 30 minutes (configurable)
- Refresh token lifetime: 7 days; rotated and blacklisted on use
- Custom authentication class: `ActiveRoleJWTAuthentication` — validates role on every request
- Login endpoint: `POST /api/auth/login/` → returns `access` + `refresh`
- Token refresh: `POST /api/auth/refresh/`
- Logout: `POST /api/auth/logout/` — blacklists refresh token
- Current user: `GET /api/auth/me/`

---

## Roles & Permissions

### User Roles

| Role | Scope | School Required |
|------|-------|----------------|
| `ADMIN` + `admin_scope=SYSTEM` | Platform-wide | No |
| `ADMIN` + `admin_scope=SCHOOL` | One school | Yes |
| `TEACHER` | One school | Yes |
| `STUDENT` | One school | Yes |
| `EXAM_MARKING_MANAGER` | Cross-school | Optional |
| `CHIEF_EXAMINER` | Cross-school | Optional |
| `EXAMINER` | One school | Yes |
| `CHECKER` | One school | Yes |
| `DATA_ENTRY_CLERK` | One school | Yes |
| `IT_SUPPORT` | One school | Yes |

A `TEACHER` can hold a `secondary_role` of `EXAMINER` or `CHECKER` — used for teachers who also serve as exam markers.

### Critical Security Constraint

`SYSTEM_ADMIN` must **NOT** have routine operational permissions to:
- Create, edit, open, close, or manage marking sessions
- Manage marking teams or assign examiners
- Allocate or reallocate scripts
- Edit marking schemes or standardisation decisions
- Perform examiner, checker, moderation, or reconciliation actions
- Finalize marking sessions or approve result exports

Authorization is enforced at the **Django backend** on every protected endpoint, not just through frontend visibility.

---

## Throttling

| Scope | Rate (production) |
|-------|------------------|
| Anonymous | 30/min |
| Authenticated user | 120/min |
| Login endpoint | 5/min |
| AI explanation | 10/min |

Throttling is **disabled in development** (`development.py` sets `DEFAULT_THROTTLE_CLASSES = []`).

---

## API Endpoints

All endpoints are prefixed with `/api/`.

### Auth — `/api/auth/`

| Method | Path | Description |
|--------|------|-------------|
| POST | `login/` | Obtain JWT tokens |
| POST | `refresh/` | Refresh access token |
| POST | `logout/` | Blacklist refresh token |
| GET | `me/` | Current user profile |
| GET | `school-teachers/` | List teachers in same school |
| PATCH | `school-teachers/<id>/examiner/` | Assign/remove examiner role |

### Schools — `/api/schools/`

| Method | Path | Description | Role |
|--------|------|-------------|------|
| GET/POST | `/` | List / create schools | SYSTEM_ADMIN |
| GET/PATCH/DELETE | `<id>/` | Retrieve / update / delete school | SYSTEM_ADMIN |
| POST | `<id>/activate/` | Activate school | SYSTEM_ADMIN |
| POST | `<id>/deactivate/` | Deactivate school | SYSTEM_ADMIN |
| GET/POST | `<id>/admins/` | List / add school admins | SYSTEM_ADMIN |
| GET/POST | `<id>/teachers/` | List / add teachers | SCHOOL_ADMIN |
| GET/POST | `<id>/students/` | List / add students | SCHOOL_ADMIN |

### Academics — `/api/academics/`

| Method | Path | Description |
|--------|------|-------------|
| GET | `class-levels/` | List all class levels (e.g. Form 1–6) |
| GET/POST | `subjects/` | List / create subjects (per school) |
| GET/PATCH/DELETE | `subjects/<id>/` | Manage subject |
| GET/POST | `topics/` | List / create topics |
| GET/PATCH/DELETE | `topics/<id>/` | Manage topic |
| GET/POST | `learning-objectives/` | List / create learning objectives |
| GET/PATCH/DELETE | `learning-objectives/<id>/` | Manage learning objective |

### Classes — `/api/classes/`

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/` | List / create school classes |
| GET/PATCH/DELETE | `<id>/` | Manage class |
| GET/POST/DELETE | `<id>/teachers/` | Assign / remove teachers |
| GET/POST/DELETE | `<id>/students/` | Enroll / remove students |
| POST | `join/` | Student joins class via `class_code` |

### Lessons — `/api/lessons/`

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/` | List / create lessons |
| GET/PATCH/DELETE | `<id>/` | Manage lesson |
| POST | `<id>/publish/` | Publish lesson |
| POST | `<id>/archive/` | Archive lesson |
| GET/POST/DELETE | `<id>/assign/` | Assign / unassign lesson to classes |
| GET/POST/DELETE | `<id>/attachments/` | Manage lesson attachments (files) |
| GET | `<id>/structure/` | Full lesson structure (sections + blocks) |
| GET/POST | `sections/` | List / create sections |
| GET/PATCH/DELETE | `sections/<id>/` | Manage section |
| GET/POST/DELETE | `sections/<id>/content-blocks/` | Manage content blocks |
| GET/POST | `sections/<id>/mini-quiz/` | Get / create section mini-quiz |
| GET | `sections/<id>/content/` | Full section content |
| POST | `sections/<id>/submit-quiz/` | Submit mini-quiz answers |

**Content Block Types:** `HEADING`, `TEXT`, `IMAGE`, `KEY_POINT`

### Quizzes — `/api/quizzes/`

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/` | List / create quizzes |
| GET/PATCH/DELETE | `<id>/` | Manage quiz |
| POST | `<id>/publish/` | Publish quiz |
| POST | `<id>/archive/` | Archive quiz |
| GET/POST/DELETE | `<id>/assign/` | Assign / unassign quiz to classes |
| GET | `<id>/student-questions/` | Questions visible to student (no answers) |
| GET/POST | `questions/` | List / create questions |
| GET/PATCH/DELETE | `questions/<id>/` | Manage question |
| GET/POST/DELETE | `questions/<id>/options/` | Manage MCQ / True-False options |
| GET/POST/DELETE | `questions/<id>/pairs/` | Manage matching pairs |
| POST | `attempts/start/` | Start a quiz attempt |
| POST | `attempts/<id>/answer/` | Submit an answer |
| POST | `attempts/<id>/submit/` | Finalize attempt and get score |
| GET | `attempts/` | List student's own attempts |
| POST | `attempts/sync/` | Offline sync — push attempt(s) taken offline |

**Question Types:** `MCQ`, `TRUE_FALSE`, `MATCHING`

### Progress — `/api/progress/`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | List student's lesson progress |
| GET | `<id>/` | Retrieve one progress record |
| POST | `track/` | Update progress (upsert — offline-safe) |

### Exams — `/api/exams/`

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/` | List / create exams |
| GET/PATCH/DELETE | `<id>/` | Manage exam |
| PATCH | `<id>/section-settings/` | Configure optional section rules |
| POST | `<id>/publish/` | Publish exam |
| POST | `<id>/close/` | Close exam |
| POST | `<id>/start-marking/` | Move exam to MARKING status |
| GET | `<id>/mark-sheet/` | Download mark sheet (all candidates + scores) |
| GET | `<id>/cover-pages/` | Download candidate cover pages |
| GET/POST/DELETE | `<id>/candidates/` | Manage exam candidates |
| POST | `<id>/bulk-candidates/` | Add multiple candidates at once |
| POST | `<id>/exam-paper/upload/` | Upload exam paper PDF |
| POST | `<id>/marking-scheme/upload/` | Upload marking scheme document |
| GET/POST | `<id>/scripts/` | List / upload scripts |
| GET | `<id>/attempts/` | List all attempts for exam |
| POST | `bulk-publish-results/` | Publish results for multiple exams |
| GET | `marking-summary/` | Summary of marking progress |
| POST | `<id>/publish-results/` | Publish exam results to students |
| GET/POST | `questions/` | List / create exam questions |
| GET/PATCH/DELETE | `questions/<id>/` | Manage exam question |
| GET/POST | `scripts/` | List all scripts |
| GET/PATCH/DELETE | `scripts/<id>/` | Manage script |
| POST | `scripts/<id>/ocr/` | Run OCR on script |
| POST | `scripts/<id>/ai-mark/` | Run AI marking on script |
| POST | `scripts/<id>/submit-for-review/` | Move script to teacher review |
| POST | `scripts/<id>/finalize/` | Finalize script marks |
| PATCH | `answers/<id>/` | Teacher marks one answer |
| GET | `results/` | List exam results |
| GET | `audit/` | Marking audit log |

**Exam statuses:** `DRAFT → PUBLISHED → CLOSED → MARKING → REVIEW → FINALIZED → RESULTS_PUBLISHED`

**Script statuses:** `UPLOADED → PROCESSING → MARKED → TEACHER_REVIEW → FINALIZED → PUBLISHED`

### AI — `/api/ai/`

| Method | Path | Description |
|--------|------|-------------|
| GET | `health/` | Check AI service (Ollama/OpenAI) availability |
| POST | `explain-mistake/` | Request AI explanation for a wrong answer |
| POST | `explain-mistake/<id>/retry/` | Retry a failed explanation request |

AI falls back: OpenAI GPT-4o (if key present) → Ollama (local, free, works offline).

### Analytics — `/api/analytics/`

**Teacher endpoints** (own school, own classes):

| Path | Description |
|------|-------------|
| `teacher/classes/` | Performance per class |
| `teacher/students/` | Performance per student |
| `teacher/quizzes/` | Quiz completion and scores |
| `teacher/quizzes/<id>/questions/` | Per-question performance for one quiz |
| `teacher/lessons/` | Lesson completion rates |
| `teacher/exams/` | Exam results summary |

**School Admin endpoints**:

| Path | Description |
|------|-------------|
| `school/overview/` | Student count, teacher count, avg scores |
| `school/classes/` | Average quiz score per class |
| `school/subjects/` | Average quiz score per subject |

**System Admin endpoints**:

| Path | Description |
|------|-------------|
| `system/schools/` | All schools summary |
| `system/users/` | Platform-wide user counts |
| `system/user-directory/` | Full user listing |
| `system/usage/` | Platform usage statistics |

### Sync — `/api/sync/`

| Method | Path | Description |
|--------|------|-------------|
| POST | `push/` | Push offline-created records (attempts, progress) |
| GET | `pull/` | Pull updates since last sync timestamp |

The sync layer adds idempotency on top of each entity's own idempotency key. Pushing the same `client_id` twice is safe — the second push is a no-op.

### Admin Panel — `/api/admin/`

Django admin site at `/admin/`. REST admin user management at `/api/admin/` (school admin use).

---

## Data Models

### `School`
Core tenant. Every school-scoped resource has a `school` FK.

Fields: `name`, `code` (unique), `address`, `phone_number`, `email`, `is_active`

---

### `User` (custom `AbstractUser`)
Extended Django user with role-based access.

Key fields: `role`, `secondary_role`, `admin_scope`, `phone_number`, `school`

DB-level constraints enforce: system admins have no school, school admins must have a school, students/teachers must have a school.

---

### `ClassLevel`
System-wide (not per school). Examples: Form 1, Form 2, Form 3, Form 4.

---

### `Subject`
Per school. Unique by `(school, name)`.

---

### `Topic`
Per school + subject + class level. Ordered.

---

### `LearningObjective`
Per topic. Granular curriculum mapping point.

---

### `SchoolClass`
One class per academic year. Auto-generates a unique `class_code` for student join flow.

Memberships: `TeacherClassMembership`, `StudentClassMembership`

---

### `Lesson`
Statuses: `DRAFT → PUBLISHED → ARCHIVED`

Structure: Lesson → LessonSection → SectionContentBlock (HEADING/TEXT/IMAGE/KEY_POINT)

A section can have one `SectionMiniQuiz` linking to a `Quiz`.

A lesson can have `LessonAttachment` files (PDF, images — max 10 MB each).

---

### `Quiz`
Statuses: `DRAFT → PUBLISHED → ARCHIVED`

Question types: `MCQ` (multiple choice), `TRUE_FALSE`, `MATCHING`

- MCQ/True-False: uses `QuestionOption` (one marked correct)
- Matching: uses `MatchingPair` (left ↔ right key)

Marked **deterministically** — no AI involved in quiz scoring.

---

### `StudentQuizAttempt`
`client_attempt_id` generated by the device — idempotency key for offline sync.

Statuses: `IN_PROGRESS → SUBMITTED` (live) or `SYNC_PENDING → SYNCED/FAILED` (offline)

Scoring: `score`, `total_marks`, `percentage` populated by `QuizMarkingService`.

---

### `LessonProgress`
One row per `(student, lesson)`. `last_accessed_at` is client-reported (offline-safe). `progress_percentage` 0–100.

---

### `Exam`
Statuses: `DRAFT → PUBLISHED → CLOSED → MARKING → REVIEW → FINALIZED → RESULTS_PUBLISHED`

A physical paper exam. Has `ExamCandidate` rows (student ↔ candidate number), `ExamQuestion` rows, and `ExamScript` uploads.

`section_settings` (JSON): optional rules for choice sections (e.g. "answer any 3 of Section B").

---

### `ExamScript`
A scanned student script (PDF/PNG/JPEG, max 25 MB).

Re-uploading for the same candidate creates a new row with incremented `version` — preserves audit trail.

`ExamScriptPage`: each page rendered as PNG for teacher review.

OCR statuses: `NOT_STARTED → PROCESSING → READY / FAILED`

---

### `StudentExamAttempt`
One per `(exam, candidate)`. Links to current script version. Stores `score`, `total_marks`, `percentage`.

---

### `StudentExamAnswer`
One per `(attempt, exam_question)`.

- `recorded_answer` — the text extracted by OCR or entered manually
- `ai_marks_awarded` / `ai_feedback` — AI's suggestion for subjective questions
- `teacher_final_marks` / `teacher_comment` — authoritative human-confirmed mark
- For objective questions, marks are set deterministically (no AI)

---

### `MarkingAuditLog`
Append-only. Records every state change in the marking domain: script submissions, finalizations, answer markings, OCR runs, assignments. Snapshots `actor_username` and `actor_role` at write time so the log survives account changes.

---

### `AIExplanationRequest`
One per `(student, StudentQuizAnswer)`. Lifecycle: `PENDING → PROCESSING → COMPLETED / FAILED`.

Results are cached in `MistakeExplanationCache` by `(question, wrong_answer_signature)` — common mistakes are reused across students, saving API calls.

---

### `SyncRecord`
Framework-level idempotency tracking for the sync push endpoint. Keyed by `(user, entity_type, client_id)`. Statuses: `PENDING → SYNCING → SYNCED / FAILED`.

---

## OCR & AI Marking Pipeline

### OCR Flow (`POST /api/exams/scripts/<id>/ocr/`)

1. Script file (PDF/image) is passed to `vision_extract_answers()`
2. Each page is rendered as PNG and sent directly to GPT-4o Vision with the question list
3. GPT-4o returns a JSON mapping `question_number → answer_text`
4. For any unmapped questions, falls back to `extract_script()` (text extraction) + `map_answers()` (AI text analysis)
5. `map_answers()` tries: header-split → AI extraction → regex (in order)
6. Results populate `StudentExamAnswer.recorded_answer`

Token limits: GPT-4o `max_tokens=16000`, Ollama `num_predict=8000` — set high to prevent JSON cutoff for long-answer scripts.

### AI Marking Flow (`POST /api/exams/scripts/<id>/ai-mark/`)

1. For each `SUBJECTIVE` question, AI evaluates `recorded_answer` against the marking scheme
2. Sets `ai_marks_awarded` and `ai_feedback` on `StudentExamAnswer`
3. For `OBJECTIVE` questions, marks are set deterministically by comparing `recorded_answer` to `correct_answer`
4. Script status moves to `MARKED`

### Teacher Review

- Teacher reviews each answer, can accept AI suggestions or override
- "Accept All" button: accepts all pending AI suggestions in one action
- `teacher_final_marks` is the authoritative mark — AI suggestion is never final on its own
- Finalize: sets script to `FINALIZED`, calculates total `score` and `percentage`

### AI Fallback Chain

```
OpenAI GPT-4o (if OPENAI_API_KEY set)
    ↓ (on failure)
Ollama gemma3:4b / qwen2.5:3b (local, no internet needed)
    ↓ (on failure)
Pytesseract (text-only, no AI)
```

---

## Offline Sync Architecture

Students and teachers work offline. Data is stored in the browser's IndexedDB (frontend) and synced when connection is restored.

**Push** (`POST /api/sync/push/`): client sends a list of locally-created records (quiz attempts, lesson progress, AI explanation requests). Each item has a `client_id` (device-generated UUID) used as the idempotency key — pushing the same item twice is safe.

**Pull** (`GET /api/sync/pull/?since=<timestamp>`): server returns all records updated since the given timestamp for the current user's school.

**SyncRecord** model tracks every push operation's status so failures are visible and retryable.

---

## Multi-tenancy

Every school's data is isolated through a `school` FK on every resource. The `TenantOwnedViewSetMixin` and `TenantScopedQuerysetMixin` enforce that:
- A user can only see data belonging to their own school
- Creating a resource in another school's name is rejected
- System admins can see all schools' data for oversight purposes only

---

## Middleware Stack (in order)

1. `GZipMiddleware` — compresses responses
2. `SecurityMiddleware`
3. `CorsMiddleware` — handles CORS headers
4. `SessionMiddleware`
5. `CommonMiddleware`
6. `CsrfViewMiddleware`
7. `AuthenticationMiddleware`
8. `MessageMiddleware`
9. `XFrameOptionsMiddleware`

---

## File Storage

Files are never stored as blobs in PostgreSQL. Only metadata (`file_name`, `mime_type`, `file_size`, `checksum`) is in the database; the actual bytes live in the file system under `MEDIA_ROOT`.

| File type | Upload path |
|-----------|------------|
| Lesson attachments | `lesson_attachments/<school_id>/<lesson_id>/` |
| Exam scripts | `exam_scripts/<school_id>/<exam_id>/<candidate_id>/` |
| Script page images | `exam_script_pages/<school_id>/<exam_id>/<candidate_id>/` |
| Marking scheme docs | `exam_marking_schemes/<year>/<month>/` |

`file_size`, `mime_type`, and `checksum` are always computed server-side at upload time — never trusted from client input.

---

## Health Check

`GET /api/health/` — returns `{"status": "ok"}`. Used by load balancers and monitoring.
