import json
import re
import os
import shutil
from pathlib import Path
from datetime import datetime

# Configuration
PROJECT_ROOT = Path(__file__).parent.parent
CODE_DIR = PROJECT_ROOT / "code"
VERSION_JSON = CODE_DIR / "version.json"
MAIN_PY = CODE_DIR / "main.py"
CHANGELOG = PROJECT_ROOT / "CHANGELOG.md"
BACKUPS_DIR = PROJECT_ROOT / "backups"

def get_current_version():
    with open(VERSION_JSON, 'r', encoding='utf-8') as f:
        data = json.load(f)
        return data.get("version", "1.5.2")

def update_version_json(new_version):
    with open(VERSION_JSON, 'r+', encoding='utf-8') as f:
        data = json.load(f)
        data["version"] = new_version
        f.seek(0)
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.truncate()

def update_main_py(new_version):
    content = MAIN_PY.read_text(encoding='utf-8')
    # Find APP_VERSION = "..." or similar
    new_content = re.sub(r'APP_VERSION\s*=\s*".*?"', f'APP_VERSION = "{new_version}"', content)
    MAIN_PY.write_text(new_content, encoding='utf-8')

def create_backup(version):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUPS_DIR / f"v{version}_{timestamp}"
    backup_path.mkdir(parents=True, exist_ok=True)
    
    # Copy all .py files and configuration files
    for file in CODE_DIR.glob("*.py"):
        shutil.copy(file, backup_path)
    for file in CODE_DIR.glob("*.json"):
        shutil.copy(file, backup_path)
    if CHANGELOG.exists():
        shutil.copy(CHANGELOG, backup_path)
        
    print(f"Backup created at: {backup_path}")
    return backup_path

def rollback_instruction():
    print("\n--- Rollback Instructions ---")
    print("1. Locate your desired version in the 'backups' folder.")
    print("2. Copy the files from the backup folder back into the 'code' directory.")
    print("3. Restart the application.")
    print("--------------------------------\n")

def bump_version(new_version):
    old_version = get_current_version()
    print(f"Bumping version: {old_version} -> {new_version}")
    
    # Create backup of OLD version before bumping
    create_backup(old_version)
    
    update_version_json(new_version)
    update_main_py(new_version)
    
    print(f"Successfully bumped to v{new_version}")
    rollback_instruction()

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python version_manager.py [new_version] | backup | rollback")
        sys.exit(1)
        
    cmd = sys.argv[1].lower()
    if cmd == "backup":
        create_backup(get_current_version())
    elif cmd == "rollback":
        rollback_instruction()
    else:
        # Assume it's a version string
        bump_version(cmd)
