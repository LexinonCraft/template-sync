"""
Utility functions for working with Git repositories.
"""

from abc import ABC, abstractmethod
from pathlib import Path

import git

default_revisions = ["main", "master"]


def open_git_repo(repo_path: Path) -> git.Repo | None:
    """Try to open a Git repository at the given path.

    Args:
        repo_path: Path to the Git repository.

    Returns:
        Repo object if the path is a valid Git repository, otherwise None.
    """
    try:
        return git.Repo(repo_path)
    except Exception:  # noqa: BLE001
        return None


def get_commit(repo: git.Repo, rev: str) -> git.Commit | None:
    """
    Get a commit object for the given revision in the repository if it exists.

    Args:
        repo: The Git repository.
        rev: The revision (commit hash, branch name, tag, etc.) to look up.

    Returns:
        Commit object if the revision exists, otherwise None.
    """
    try:
        commit = repo.commit(rev)
        return commit
    except Exception:  # noqa: BLE001
        return None


class AbstractRepo(ABC):
    """
    Abstract base class for Git and non-Git (i.e. filesystem) repositories.
    """

    def __init__(self, root_path: Path):
        self.root_path = root_path

    def get_root(self) -> Path:
        """
        Get the root path of the repository.

        Returns:
            Path to the root of the repository.
        """
        return self.root_path

    @abstractmethod
    def get_ref(self) -> str | None:
        """
        Get the reference (branch, tag, or commit hash) of the repository.

        Returns:
            Reference string if available, otherwise None.
        """

    @abstractmethod
    def get_commit_hash(self) -> str | None:
        """
        Get the commit hash of the repository.

        Returns:
            Commit hash string if available, otherwise None.
        """

    @abstractmethod
    def read_file_path(self, file_path: Path) -> str:
        """
        Read the contents of a file in the repository.

        Args:
            file_path: Path to the file relative to the repository root.

        Returns:
            Contents of the file as a string.
        """

    def read_file(self, file_path: str) -> str:
        """
        Read the contents of a file in the repository.

        Args:
            file_path: Path to the file relative to the repository root.

        Returns:
            Contents of the file as a string.
        """
        return self.read_file_path(Path(file_path))

    def copy_file(self, source_path: Path, target_path: Path) -> None:
        """
        Copy a file from the repository to a target path outside of the repository.

        Args:
            source_path: Path to the source file relative to the repository root.
            target_path: Path to the target file on the filesystem.
        """
        content = self.read_file_path(source_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(content)


class GitRevRepo(AbstractRepo):
    """
    Represents a Git repository at a specific revision.
    """

    def __init__(self, repo: git.Repo, root_path: Path, rev: str):
        super().__init__(root_path)
        self.repo = repo
        self.rev = rev
        self.commit = get_commit(repo, rev)
        if self.commit is None:
            raise ValueError(f"Revision '{rev}' does not exist in the repository.")

    def get_ref(self) -> str | None:
        return self.rev

    def get_commit_hash(self) -> str | None:
        return self.commit.hexsha if self.commit else None

    def read_file_path(self, file_path: Path) -> str:
        if self.commit is None:
            raise ValueError("Commit is not set. Cannot read files.")

        try:
            blob = self.commit.tree / str(file_path)
            return blob.data_stream.read().decode("utf-8")
        except KeyError:
            raise FileNotFoundError(f"File '{file_path}' does not exist in revision '{self.rev}'.")


class FileSystemRepo(AbstractRepo):
    """
    Represents a non-Git repository (i.e., just a directory on the filesystem).
    """

    def __init__(self, root_path: Path):
        super().__init__(root_path)

    def get_ref(self) -> str | None:
        return None

    def get_commit_hash(self) -> str | None:
        return None

    def read_file_path(self, file_path: Path) -> str:
        full_path = self.root_path / file_path
        if not full_path.exists():
            raise FileNotFoundError(f"File '{file_path}' does not exist in the filesystem repository.")

        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()


def get_repo(repo_path: Path, rev: str | None = None) -> AbstractRepo:
    """
    Get an AbstractRepo instance for the given path and optional revision.

    Args:
        repo_path: Path to the repository.
        rev: Optional Git revision. If provided, the repository must be a Git repo.

    Returns:
        An instance of AbstractRepo (either GitRevRepo or FileSystemRepo).
    """
    git_repo = open_git_repo(repo_path)
    if git_repo is not None:
        if rev is not None:
            return GitRevRepo(git_repo, repo_path, rev)
        else:
            for default_rev in default_revisions:
                commit = get_commit(git_repo, default_rev)
                if commit is not None:
                    return GitRevRepo(git_repo, repo_path, default_rev)
            raise ValueError(
                f"No revision specified and none of the default revisions ({', '.join(default_revisions)}) exist in the repository at '{repo_path}'."
            )
    else:
        if rev is not None:
            raise ValueError(f"Revision '{rev}' specified but '{repo_path}' is not a Git repository.")
        return FileSystemRepo(repo_path)
