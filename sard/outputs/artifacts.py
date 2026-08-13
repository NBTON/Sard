"""Safe, atomic publication of Step 6 output artifacts."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_OUTPUT_ROOT = Path("output/runs")
_SAFE_RUN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ArtifactError(Exception):
    """A safe artifact publication failure with a stable category."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class ArtifactWriteResult:
    artifact_type: str
    display_label: str
    filename: str
    absolute_path: Optional[str]
    mime_type: str
    size_bytes: int
    checksum: Optional[str]
    creation_status: str
    warnings: tuple[str, ...] = ()
    error_category: Optional[str] = None


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ArtifactManager:
    """Create a unique run directory and publish files without overwriting."""

    def __init__(
        self,
        output_root: str | Path | None = None,
        run_id: str = "",
        *,
        checksums: bool = False,
    ) -> None:
        root_value = output_root or os.environ.get("SARD_OUTPUT_ROOT") or DEFAULT_OUTPUT_ROOT
        self.root = Path(root_value).expanduser().resolve()
        self.run_id = str(run_id or "")
        if not _SAFE_RUN_RE.fullmatch(self.run_id):
            raise ArtifactError("unsafe_run_id", "Run ID must be a safe ASCII identifier.")
        self.checksums = checksums
        self.run_dir = (self.root / self.run_id).resolve()
        try:
            self.run_dir.relative_to(self.root)
        except ValueError as exc:
            raise ArtifactError("directory_traversal", "Run directory escapes the output root.") from exc
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.run_dir.mkdir()
        except FileExistsError as exc:
            raise ArtifactError("duplicate_run", f"Run directory already exists: {self.run_dir}") from exc

    def _destination(self, filename: str) -> Path:
        if not _SAFE_FILENAME_RE.fullmatch(filename):
            raise ArtifactError("unsafe_filename", "Artifact filename must use safe ASCII characters.")
        destination = (self.run_dir / filename).resolve()
        try:
            destination.relative_to(self.run_dir)
        except ValueError as exc:
            raise ArtifactError("directory_traversal", "Artifact path escapes the run directory.") from exc
        if destination.exists():
            raise ArtifactError("duplicate_artifact", f"Refusing to overwrite existing artifact: {destination}")
        return destination

    def _publish_temp(self, temp: Path, destination: Path) -> None:
        """Publish via an atomic hard-link, which cannot replace a target."""

        try:
            os.link(temp, destination)
            temp.unlink(missing_ok=True)
        except FileExistsError as exc:
            temp.unlink(missing_ok=True)
            raise ArtifactError("duplicate_artifact", f"Refusing to overwrite existing artifact: {destination}") from exc
        except OSError as exc:
            temp.unlink(missing_ok=True)
            raise ArtifactError("atomic_write", f"Atomic publication failed for {destination.name}.") from exc

    def write_bytes(
        self,
        data: bytes,
        *,
        filename: str,
        artifact_type: str,
        display_label: str,
        mime_type: str,
        warnings: tuple[str, ...] = (),
    ) -> ArtifactWriteResult:
        destination = self._destination(filename)
        temporary = self.run_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self._publish_temp(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return ArtifactWriteResult(
            artifact_type=artifact_type,
            display_label=display_label,
            filename=destination.name,
            absolute_path=str(destination),
            mime_type=mime_type,
            size_bytes=destination.stat().st_size,
            checksum=_checksum(destination) if self.checksums else None,
            creation_status="created",
            warnings=tuple(warnings),
        )

    def temporary_path(self, suffix: str) -> Path:
        """Return a same-directory unique path for a generator such as ReportLab."""

        if not suffix.startswith(".") or not _SAFE_FILENAME_RE.fullmatch("x" + suffix):
            raise ArtifactError("unsafe_filename", "Temporary suffix is unsafe.")
        return self.run_dir / f"render-{uuid.uuid4().hex}{suffix}"

    def publish_generated_file(
        self,
        source: str | Path,
        *,
        filename: str,
        artifact_type: str,
        display_label: str,
        mime_type: str,
        warnings: tuple[str, ...] = (),
    ) -> ArtifactWriteResult:
        source_path = Path(source).resolve()
        try:
            source_path.relative_to(self.run_dir)
        except ValueError as exc:
            raise ArtifactError("directory_traversal", "Generated source is outside the run directory.") from exc
        if not source_path.is_file():
            raise ArtifactError("missing_temporary_file", "Generated artifact does not exist.")
        destination = self._destination(filename)
        self._publish_temp(source_path, destination)
        return ArtifactWriteResult(
            artifact_type=artifact_type,
            display_label=display_label,
            filename=destination.name,
            absolute_path=str(destination),
            mime_type=mime_type,
            size_bytes=destination.stat().st_size,
            checksum=_checksum(destination) if self.checksums else None,
            creation_status="created",
            warnings=tuple(warnings),
        )


def failed_artifact(
    *,
    artifact_type: str,
    display_label: str,
    filename: str,
    mime_type: str,
    category: str,
    warning: str,
) -> ArtifactWriteResult:
    return ArtifactWriteResult(
        artifact_type=artifact_type,
        display_label=display_label,
        filename=filename,
        absolute_path=None,
        mime_type=mime_type,
        size_bytes=0,
        checksum=None,
        creation_status="failed",
        warnings=(warning,),
        error_category=category,
    )


def skipped_artifact(
    *,
    artifact_type: str,
    display_label: str,
    filename: str,
    mime_type: str,
    category: str,
    warning: str,
) -> ArtifactWriteResult:
    return ArtifactWriteResult(
        artifact_type=artifact_type,
        display_label=display_label,
        filename=filename,
        absolute_path=None,
        mime_type=mime_type,
        size_bytes=0,
        checksum=None,
        creation_status="skipped",
        warnings=(warning,),
        error_category=category,
    )
