"""Interactive command line interface for ValutaTrade Hub."""

import shlex


class WalletApplication:
    """Own the console loop and dispatch user commands."""

    def run(self) -> None:
        """Read and process commands until the user exits."""
        print("ValutaTrade Hub. Введите help для справки.")
        while True:
            try:
                command = input("wallet> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if self.execute(command):
                break

    def execute(self, command_line: str) -> bool:
        """Execute a command; return ``True`` when the shell should exit."""
        try:
            parts = shlex.split(command_line)
        except ValueError as error:
            print(f"Некорректная команда: {error}")
            return False

        if not parts:
            return False

        command = parts[0].lower()
        if command == "help":
            print("Доступные команды: help, exit")
        elif command in {"exit", "quit"}:
            return True
        else:
            print(f"Неизвестная команда: {command}")
        return False


def main() -> None:
    """Start the interactive command line."""
    WalletApplication().run()
