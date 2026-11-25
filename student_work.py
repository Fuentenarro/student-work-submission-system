"""Console application for the student laboratory work workflow."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path


DB_PATH = Path(os.environ.get("STUDENT_WORK_DB", ".student_work.sqlite3"))


class WorkflowError(Exception):
    """Raised when a requested workflow transition is not allowed."""


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                course TEXT NOT NULL,
                deadline TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assignment_id INTEGER NOT NULL REFERENCES assignments(id),
                student_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                work_text TEXT NOT NULL,
                submitted_at TEXT NOT NULL,
                status TEXT NOT NULL,
                feedback TEXT NOT NULL DEFAULT '',
                score INTEGER,
                UNIQUE (assignment_id, student_id, version)
            );
            """
        )


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def add_assignment(args: argparse.Namespace) -> None:
    try:
        deadline = date.fromisoformat(args.deadline)
    except ValueError as error:
        raise WorkflowError("срок укажите в формате ГГГГ-ММ-ДД") from error

    with connect() as connection:
        cursor = connection.execute(
            "INSERT INTO assignments (title, course, deadline, created_at) VALUES (?, ?, ?, ?)",
            (args.title.strip(), args.course.strip(), deadline.isoformat(), timestamp()),
        )
        print(f"Задание создано: ID {cursor.lastrowid}")


def list_assignments(_: argparse.Namespace) -> None:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT a.id, a.course, a.title, a.deadline, COUNT(s.id) AS submission_count
            FROM assignments AS a
            LEFT JOIN submissions AS s ON s.assignment_id = a.id
            GROUP BY a.id
            ORDER BY a.id
            """
        ).fetchall()

    if not rows:
        print("Заданий пока нет")
        return
    for row in rows:
        print(
            f"{row['id']} | {row['course']} | {row['title']} | "
            f"срок {row['deadline']} | версий работ {row['submission_count']}"
        )


def submit_work(args: argparse.Namespace) -> None:
    with connect() as connection:
        assignment = connection.execute(
            "SELECT id FROM assignments WHERE id = ?", (args.assignment_id,)
        ).fetchone()
        if assignment is None:
            raise WorkflowError(f"задание с ID {args.assignment_id} не найдено")

        previous = connection.execute(
            """
            SELECT version, status FROM submissions
            WHERE assignment_id = ? AND student_id = ?
            ORDER BY version DESC LIMIT 1
            """,
            (args.assignment_id, args.student_id.strip()),
        ).fetchone()

        if previous is not None and previous["status"] != "revision_requested":
            raise WorkflowError(
                "новая версия принимается после статуса revision_requested"
            )

        version = 1 if previous is None else previous["version"] + 1
        cursor = connection.execute(
            """
            INSERT INTO submissions
                (assignment_id, student_id, version, work_text, submitted_at, status)
            VALUES (?, ?, ?, ?, ?, 'submitted')
            """,
            (
                args.assignment_id,
                args.student_id.strip(),
                version,
                args.text.strip(),
                timestamp(),
            ),
        )
        print(f"Версия {version} отправлена: ID работы {cursor.lastrowid}")


def list_submissions(args: argparse.Namespace) -> None:
    filters = []
    values: list[object] = []
    if args.assignment_id is not None:
        filters.append("s.assignment_id = ?")
        values.append(args.assignment_id)
    if args.student_id:
        filters.append("s.student_id = ?")
        values.append(args.student_id.strip())
    where = " WHERE " + " AND ".join(filters) if filters else ""

    with connect() as connection:
        rows = connection.execute(
            """
            SELECT s.id, s.assignment_id, a.title, s.student_id, s.version,
                   s.status, s.score, s.submitted_at
            FROM submissions AS s
            JOIN assignments AS a ON a.id = s.assignment_id
            """
            + where
            + " ORDER BY s.assignment_id, s.student_id, s.version",
            values,
        ).fetchall()

    if not rows:
        print("Работ пока нет")
        return
    for row in rows:
        score = "не выставлена" if row["score"] is None else str(row["score"])
        print(
            f"ID {row['id']} | задание {row['assignment_id']}: {row['title']} | "
            f"студент {row['student_id']} | версия {row['version']} | "
            f"статус {row['status']} | балл {score}"
        )


def review_work(args: argparse.Namespace) -> None:
    with connect() as connection:
        submission = connection.execute(
            "SELECT status FROM submissions WHERE id = ?", (args.submission_id,)
        ).fetchone()
        if submission is None:
            raise WorkflowError(f"работа с ID {args.submission_id} не найдена")
        if submission["status"] not in {"submitted", "revision_requested"}:
            raise WorkflowError("проверить можно только отправленную версию")

        connection.execute(
            "UPDATE submissions SET status = ?, feedback = ?, score = NULL WHERE id = ?",
            (args.decision, args.comment.strip(), args.submission_id),
        )
        print(f"Результат проверки сохранен для работы ID {args.submission_id}")


def record_grade(args: argparse.Namespace) -> None:
    if not 0 <= args.score <= 100:
        raise WorkflowError("оценка должна быть от 0 до 100 баллов")

    with connect() as connection:
        submission = connection.execute(
            "SELECT status FROM submissions WHERE id = ?", (args.submission_id,)
        ).fetchone()
        if submission is None:
            raise WorkflowError(f"работа с ID {args.submission_id} не найдена")
        if submission["status"] != "reviewed":
            raise WorkflowError("оценку можно записать после завершения проверки")

        connection.execute(
            "UPDATE submissions SET status = 'graded', score = ? WHERE id = ?",
            (args.score, args.submission_id),
        )
        print(f"Оценка {args.score} баллов сохранена для работы ID {args.submission_id}")


def show_status(args: argparse.Namespace) -> None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT s.id, s.student_id, s.version, s.status, s.submitted_at,
                   s.feedback, s.score, a.title, a.deadline
            FROM submissions AS s
            JOIN assignments AS a ON a.id = s.assignment_id
            WHERE s.id = ?
            """,
            (args.submission_id,),
        ).fetchone()

    if row is None:
        raise WorkflowError(f"работа с ID {args.submission_id} не найдена")
    score = "не выставлена" if row["score"] is None else str(row["score"])
    feedback = row["feedback"] or "нет"
    print(f"Задание: {row['title']} (срок {row['deadline']})")
    print(f"Студент: {row['student_id']}, версия: {row['version']}")
    print(f"Статус: {row['status']}, балл: {score}")
    print(f"Комментарий: {feedback}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Консольная система сдачи лабораторных работ"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser("init", help="создать локальную базу данных")
    init_parser.set_defaults(handler=lambda _: print(f"База данных: {DB_PATH.resolve()}"))

    add_parser = commands.add_parser("assignment-add", help="опубликовать задание")
    add_parser.add_argument("--title", required=True)
    add_parser.add_argument("--course", required=True)
    add_parser.add_argument("--deadline", required=True)
    add_parser.set_defaults(handler=add_assignment)

    assignments_parser = commands.add_parser("assignments", help="показать задания и сроки")
    assignments_parser.set_defaults(handler=list_assignments)

    submit_parser = commands.add_parser("submit", help="отправить версию работы")
    submit_parser.add_argument("--assignment-id", type=int, required=True)
    submit_parser.add_argument("--student-id", required=True)
    submit_parser.add_argument("--text", required=True)
    submit_parser.set_defaults(handler=submit_work)

    submissions_parser = commands.add_parser("submissions", help="показать отправленные работы")
    submissions_parser.add_argument("--assignment-id", type=int)
    submissions_parser.add_argument("--student-id")
    submissions_parser.set_defaults(handler=list_submissions)

    review_parser = commands.add_parser("review", help="сохранить результат проверки")
    review_parser.add_argument("--submission-id", type=int, required=True)
    review_parser.add_argument(
        "--decision", choices=("reviewed", "revision_requested"), required=True
    )
    review_parser.add_argument("--comment", required=True)
    review_parser.set_defaults(handler=review_work)

    grade_parser = commands.add_parser("grade", help="зафиксировать оценку")
    grade_parser.add_argument("--submission-id", type=int, required=True)
    grade_parser.add_argument("--score", type=int, required=True)
    grade_parser.set_defaults(handler=record_grade)

    status_parser = commands.add_parser("status", help="показать статус работы")
    status_parser.add_argument("--submission-id", type=int, required=True)
    status_parser.set_defaults(handler=show_status)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    initialize_database()
    try:
        args.handler(args)
    except (WorkflowError, sqlite3.Error) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
