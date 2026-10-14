"""Entry point for the currency wallet application."""

from finalproject_nikolaev_m26_555.cli import WalletApplication


def main() -> None:
    """Start the interactive wallet command line."""
    WalletApplication().run()


if __name__ == "__main__":
    main()
