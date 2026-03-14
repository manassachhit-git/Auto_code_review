import os
os.environ["NO_PROXY"] = "*"
import gitlab
import re
import sys
import requests

# ====================================================
# Step 1: Configuration and Environment Setup
# ====================================================

GITLAB_URL = "https://gitlab.otxlab.net/"
API_TOKEN = "****GITLAB_API_TOKEN******"
GITLAB_PROJECT_ID = os.environ.get("CI_MERGE_REQUEST_PROJECT_ID")
MR_IID = os.environ.get("CI_MERGE_REQUEST_IID") 
API_URL = "https://apisix-dp.athena-preprod.otxlab.net/vertex/chat/completions"
API_KEY = "****AMS_API_KEY******"
AI_MODEL = "meta/llama-3.3-70b-instruct-maas"

# ====================================================
# Step 2: LLM Review Function
# ====================================================

def review_diff_with_llama(diff: str) -> str:
    """
    Sends the diff to the LLM API for code review and returns the review text.
    """
    headers = {
        "authorization": f"Bearer {API_KEY}",
        "content-type": "application/json"
    }

#    prompt = (
#        "You are a C# senior code reviewer. Review the following diff and provide a concise list of issues and improvements.\n"
#        "Focus only on: correctness, bugs, security risks, performance concerns, readability, and missing tests.\n"
#        "Avoid generic or high-level advice.\n"
#        "Be specific, brief, and actionable.\n"
#        "Limit the output to a maximum of 8 bullet points.\n\n"
#        f"Diff:\n{diff}\n"
#    )
    prompt = (
        "Analyze this merge request diff and return:\n"
        "1. Top 5 issues that should be fixed before merging. \n"
        "2. Suggested code improvements. \n"
        "3. Missing test cases.\n"
        "Be concise and avoid restating the diff.\n\n"
        f"Diff:\n{diff}\n"
    )

    data = {
        "model": AI_MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    try:
        response = requests.post(
            API_URL,
            headers=headers,
            json=data,
            timeout=60,
            proxies={"http": None, "https": None}
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"LLM review failed: {e}"

# ====================================================
# Step 3: Diff Parser — Extract Valid Inline Comment Positions
# ====================================================

def get_inline_positions(diff_text):
    """
    Parses the diff and returns a list of line numbers where inline comments can be added.
    Only added lines (+) are valid for inline comments.
    """
    positions = []
    new_line_num = None
    for line in diff_text.split("\n"):
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            if m:
                new_line_num = int(m.group(1))
        elif line.startswith("+") and not line.startswith("+++"):
            positions.append(new_line_num)
            new_line_num += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        else:
            if new_line_num is not None:
                new_line_num += 1
    return positions

# ====================================================
# Step 4: Main Execution — Connect to GitLab and Review MR
# ====================================================

def main():
    """
    Main workflow:
    1. Validate API credentials.
    2. Connect to GitLab and fetch MR changes.
    3. For each changed file, post file-level AI review.
    4. Post overall MR review as a note.
    """
    if not API_TOKEN or not API_KEY:
        print("Missing API token or key.")
        sys.exit(1)

    gl = gitlab.Gitlab(
        GITLAB_URL,
        private_token=API_TOKEN
    )
    project = gl.projects.get(GITLAB_PROJECT_ID)
    mr = project.mergerequests.get(MR_IID)
    changes = mr.changes()
    diff_refs = mr.diff_refs

    # Step 4.1: File-level comments
    for change in changes["changes"]:
        file_path = change["new_path"]
        diff_text = change["diff"]
        old_path = change["old_path"]

        # Skip certain files
        if file_path in ("Infrastructure/CICD/.gitlab-ci.yml", "Infrastructure/CICD/ai_code_review.py"):
            continue

        print(f"Reviewing file: {file_path}")
        ai_review = review_diff_with_llama(diff_text)
        added_lines = get_inline_positions(diff_text)

        if not added_lines:
            print(f"No valid inline positions for {file_path}. Skipping inline.")
            continue

        inline_line = added_lines[0]
        try:
            mr.discussions.create({
                "body": f"**AI Code Review**\n\n{ai_review}",
                "position": {
                    "base_sha": diff_refs["base_sha"],
                    "start_sha": diff_refs["start_sha"],
                    "head_sha": diff_refs["head_sha"],
                    "position_type": "file",
                    "new_path": file_path,
                    "old_path": old_path
                }
            })
            print(f"Posted file-level comment for {file_path}")
        except Exception as e:
            print(f"Failed to post inline comment: {e}")

    # Step 4.2: Overall MR comment
    all_diffs = "\n\n".join(change["diff"] for change in changes["changes"])
    overall_review = review_diff_with_llama(all_diffs)
    try:
        mr.notes.create({"body": f"**AI Overall Code Review**\n\n{overall_review}"})
        print("Posted overall MR comment successfully!")
    except Exception as e:
        print(f"Failed to post overall MR comment: {e}")

# ====================================================
# Step 5: Entry Point
# ====================================================

if __name__ == "__main__":
    main()