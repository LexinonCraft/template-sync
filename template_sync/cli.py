import click

@click.group()
def entrypoint():
    """Entry point for the CLI"""
    pass

if __name__ == "__main__":
    entrypoint()
