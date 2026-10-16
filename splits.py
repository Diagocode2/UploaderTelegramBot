import multivolumefile
import py7zr
from pathlib import Path

def split_files(input_file: str, part_size_bytes: int, output_dir: str = ".") -> list[str]:
    """
    Split a file into compressed multi-volume (.7z) parts.

    Parameters
    ----------
    input_file : str
        Path to the original file.
    part_size_bytes : int
        Maximum size of each part in bytes.
    output_dir : str, optional
        Directory where the split files will be saved (default current folder).

    Returns
    -------
    list[str]
        List of paths to the generated split files.
    """
    file_path = Path(input_file)
    if not file_path.is_file():
        raise FileNotFoundError(f"File not found: {input_file}")

    if part_size_bytes <= 0:
        raise ValueError("Part size must be greater than 0.")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    archive_name = output_path / (file_path.stem + ".7z")

    # Create multi-volume archive
    with multivolumefile.open(str(archive_name), mode='wb', volume_size=part_size_bytes) as target_archive:
        with py7zr.SevenZipFile(target_archive, 'w') as archive:
            archive.write(str(file_path), arcname=file_path.name)

    # Collect generated volumes
    volumes = sorted([
        str(f)
        for f in output_path.iterdir()
        if f.name.startswith(file_path.stem + ".7z.")
    ])

    if not volumes:
        raise RuntimeError("No volumes were generated. Check parameters.")

    return volumes