# AnimePahe Auto-Downloader

A powerful set of scripts and modules designed to automate the process of tracking and downloading anime from AnimePahe.

## Features

- **Automated Tracking**: Scans your configured library directory to identify existing anime folders and automatically checks for new episodes.
- **Flexible Modes**:
  - **Manual Download**: Pass specific anime names to download them directly.
  - **Season Scanning**: Use `--more-seasons` or `--new-seasons` to automatically find sequels, movies, and new seasons for anime you already have.
  - **URL Support**: Use the `--url` flag to specify a direct AnimePahe series page if search results are ambiguous.
- **Customizable Quality**: Supports 360p, 720p, and 1080p downloads.
- **Audio Options**: Toggle between English Dub (`en`) and Japanese Sub (`jap`).
- **Mirror Support**: Automatically rotates through available AnimePahe and Kwik mirrors to ensure high availability.
- **Database Persistence**: Uses a local SQLite database (`tracking.db`) to remember tracked folders and avoid duplicate downloads.

## Usage

### Getting Started
Simply run the script with no arguments to perform a full library scan and update:
```powershell
python animepahe_download.py
```

### Common Commands
- **Download specific series**: `python animepahe_download.py "Frieren, Jujutsu Kaisen"`
- **Download all seasons of an anime**: `python animepahe_download.py "One Piece" --all-seasons`
- **Check for new sequel seasons**: `python animepahe_download.py --new-seasons`
- **Specify quality**: `python animepahe_download.py "Attack on Titan" -q 1080p`

## File Structure

- `animepahe_download.py`: The entry point for the application.
- `config.py`: Local settings including base download paths, default quality, and mirror lists.
- `tracking.db`: Database file tracking which anime IDs map to which local folders.
- `modules/`: Contains the core logic for web scraping, database management, and download processing.
- `animepahe_download.bat`: A convenient shortcut for running the downloader on Windows.
- [tests/](file:///d:/Projects/scripts/animepahe/tests): The unit testing suite directory.

## Running Tests

To run the unit tests, execute the following commands from the root directory:

### Run All Tests
```powershell
python -m unittest discover -s tests
```

### Run Tests with Coverage Report
Ensure you have the `coverage` package installed:
```powershell
pip install coverage
python -m coverage run -m unittest discover -s tests
python -m coverage report
```

### Run a Specific Test Module
For example, to run database tests:
```powershell
python -m unittest tests/test_db.py
```

