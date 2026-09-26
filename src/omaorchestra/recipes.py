"""Recipes: named multi-step chains (`omaorchestra recipe run <name> "task"`).

Each step is a queued task that follows the one before (chain.py), in the
same worktree, with a brief of what it did. A step has a prompt template
({task} is the task you give), and optionally an agent, model and
permission mode; `review = true` makes it a review of the work so far.

Built in: plan-then-build and build-then-review. Your own go in
~/.config/omaorchestra/recipes.toml, and one with a built-in's name
replaces it:

    [recipes.fix-and-test]
    description = "Fix it, then have another agent write the tests"

    [[recipes.fix-and-test.steps]]
    prompt = "{task}\\n\\nCommit your work when it is done."

    [[recipes.fix-and-test.steps]]
    prompt = "Write tests for the fix just made for: {task}. Commit them."
    agent = "codex"
"""

import tomllib

from . import adapters, config

BUILTIN = {
    "plan-then-build": {
        "description": "Write a plan first (to PLAN.md, changing no code), then build from it",
        "steps": [
            {"prompt": "Plan the task below; do not change any code yet. Write the plan to PLAN.md at the top of "
                       "the repository: numbered steps, with the files each one touches and how to check it. Then "
                       "stop.\n\nTask: {task}",
             "permission_mode": "acceptEdits"},
            {"prompt": "Carry out the plan in PLAN.md for this task: {task}\n\nCommit your work as you go, and "
                       "remove PLAN.md when you are done."},
        ],
    },
    "build-then-review": {
        "description": "Build it, then have an agent review the changes (the verdict shows on the worktree)",
        "steps": [
            {"prompt": "{task}\n\nCommit your work when it is done."},
            {"prompt": "Review the changes made for this task: {task}\n\nLook for bugs, missing tests, and "
                       "anything that does not do what the task asked.",
             "review": True},
        ],
    },
}
STEP_KEYS = ("prompt", "agent", "model", "permission_mode", "review", "same_worktree")


class RecipeError(Exception):
    pass


def path():
    return config.path().parent / "recipes.toml"


def _check(name, recipe):
    steps = recipe.get("steps")
    if not isinstance(steps, list) or not steps:
        raise RecipeError(f"recipe {name} has no steps")
    for n, step in enumerate(steps, 1):
        if not isinstance(step, dict) or not isinstance(step.get("prompt"), str) or "{task}" not in step["prompt"]:
            raise RecipeError(f"recipe {name}, step {n}: needs a prompt with {{task}} in it")
        unknown = set(step) - set(STEP_KEYS)
        if unknown:
            raise RecipeError(f"recipe {name}, step {n}: unknown {', '.join(sorted(unknown))}")
        if step.get("agent") and step["agent"] not in adapters.ADAPTERS:
            raise RecipeError(f"recipe {name}, step {n}: unknown agent {step['agent']}")
        if n == 1 and step.get("review"):
            raise RecipeError(f"recipe {name}: the first step cannot be a review (there is nothing to review yet)")


def load(target=None):
    """Built-in and your own recipes, by name; raises RecipeError for a bad file."""
    recipes = {name: dict(r, builtin=True) for name, r in BUILTIN.items()}
    target = target or path()
    try:
        data = tomllib.loads(target.read_text())
    except FileNotFoundError:
        return recipes
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise RecipeError(f"{target}: {e}") from None
    own = data.get("recipes") or {}
    if not isinstance(own, dict):
        raise RecipeError(f"{target}: [recipes] must be a table of recipes")
    for name, recipe in own.items():
        if not isinstance(recipe, dict):
            raise RecipeError(f"{target}: recipe {name} must be a table")
        _check(name, recipe)
        recipes[name] = {"description": recipe.get("description", ""), "steps": recipe["steps"], "builtin": False}
    return recipes


def get(name, target=None):
    recipes = load(target)
    if name not in recipes:
        raise RecipeError(f"no recipe {name} (have: {', '.join(sorted(recipes))})")
    return recipes[name]


def items(name, task, cwd, base=None, target=None):
    """The queue items for running a recipe on `task` in `cwd`, in order; the
    daemon links each to the one before (`after` is filled in as they are
    added). `base` holds fields every step shares (worktree, provider...)."""
    recipe = get(name, target)
    steps = []
    for n, step in enumerate(recipe["steps"], 1):
        item = {**(base or {}), "task": step["prompt"].replace("{task}", task.strip()), "cwd": cwd,
                "agent": step.get("agent") or (base or {}).get("agent") or "claude", "recipe": name,
                "review": bool(step.get("review")), "extra": []}
        if step.get("model"):
            item["model"] = step["model"]
        if step.get("permission_mode"):
            item["permission_mode"] = step["permission_mode"]
        if n > 1:
            item["same_worktree"] = step.get("same_worktree", True)
            item.pop("worktree", None)
        steps.append(item)
    return steps
