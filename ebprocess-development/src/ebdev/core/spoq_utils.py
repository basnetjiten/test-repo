import yaml
from pathlib import Path
from typing import List, Dict

def get_spoq_tasks(epic_dir: str) -> List[Dict]:
    """Load all task YAMLs from the epic directory."""
    tasks = []
    tasks_dir = Path(epic_dir) / "tasks"
    if not tasks_dir.exists():
        return tasks
        
    for yml_path in tasks_dir.glob("*.yml"):
        try:
            with open(yml_path, 'r') as f:
                task = yaml.safe_load(f)
                tasks.append(task)
        except Exception:
            pass
    return tasks

def update_spoq_task_status(epic_dir: str, task_id: str, status: str):
    """Update the status of a specific task."""
    yml_path = Path(epic_dir) / "tasks" / f"{task_id}.yml"
    if not yml_path.exists():
        return
        
    try:
        with open(yml_path, 'r') as f:
            task = yaml.safe_load(f)
            
        task["status"] = status
        
        with open(yml_path, 'w') as f:
            yaml.dump(task, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        print(f"Error updating task {task_id}: {e}")

def get_active_wave_tasks(epic_dir: str) -> List[Dict]:
    """Find all pending/blocked tasks that are ready to run (all dependencies completed)."""
    tasks = get_spoq_tasks(epic_dir)
    tasks_by_id = {t["id"]: t for t in tasks}
    
    ready_tasks = []
    for t in tasks:
        if t["status"] in ["pending", "blocked"]:
            deps_completed = True
            for dep_id in t.get("dependencies", []):
                dep_task = tasks_by_id.get(dep_id)
                if not dep_task or dep_task["status"] != "completed":
                    deps_completed = False
                    break
            
            if deps_completed:
                ready_tasks.append(t)
                
    return ready_tasks
