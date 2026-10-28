"""Accounts of the site from the command line, inside the backend container:

    docker compose exec backend python users.py list
    docker compose exec backend python users.py add ivan [--admin]
    docker compose exec backend python users.py passwd ivan
    docker compose exec backend python users.py remove ivan

The password is asked twice and never shown; it is not taken from the command line, so it does
not stay in the shell history.
"""

import argparse
import getpass
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import auth
from database import DATABASE_URL
from migrate import upgrade_database


def ask_password() -> str:
    first = getpass.getpass("Пароль: ")
    if first != getpass.getpass("Ещё раз: "):
        sys.exit("Пароли не совпали")
    return first


def main() -> None:
    args = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    args.add_argument("command", choices=["list", "add", "passwd", "remove"])
    args.add_argument("login", nargs="?", default="")
    args.add_argument("--admin", action="store_true", help="for add: an administrator")
    options = args.parse_args()
    if options.command != "list" and not options.login:
        args.error("нужен логин")
    engine = create_engine(DATABASE_URL)
    upgrade_database(engine)
    with sessionmaker(engine)() as session:
        try:
            if options.command == "list":
                for user in session.scalars(select(auth.User).order_by(auth.User.login)):
                    print(user.login, "(администратор)" if user.is_admin else "")
            elif options.command == "add":
                auth.create_user(session, options.login, ask_password(), options.admin)
                print("Пользователь добавлен")
            elif options.command == "passwd":
                auth.set_password(session, options.login, ask_password())
                print("Пароль изменён, все входы пользователя закрыты")
            else:
                auth.remove_user(session, options.login)
                print("Пользователь удалён")
        except ValueError as exc:
            sys.exit(f"Ошибка: {exc}")


if __name__ == "__main__":
    main()
