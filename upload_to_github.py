import os
import subprocess

# --- 請填寫你的資訊 ---
GITHUB_USER = "fathermarker666"
REPO_NAME = "SkateSafe-Final"
COMMIT_MSG = "登入系統功能完成"

def run_command(command):
    try:
        subprocess.run(command, check=True, shell=True)
    except subprocess.CalledProcessError as e:
        print(f"執行失敗: {e}")

def main():
    # 1. 初始化 Git
    print("正在初始化 Git...")
    run_command("git init")
    
    # 2. 加入所有檔案
    print("正在將檔案加入暫存區...")
    run_command("git add .")
    
    # 3. 提交
    print(f"正在提交: {COMMIT_MSG}")
    run_command(f'git commit -m "{COMMIT_MSG}"')
    
    # 4. 重新命名分支為 main
    run_command("git branch -M main")
    
    # 5. 連結遠端倉庫 (如果你還沒開過，這步會報錯，記得先去 GitHub 網頁開一個空的 Repo)
    # 網址格式: https://github.com/使用者/倉庫名.git
    remote_url = f"https://github.com/{GITHUB_USER}/{REPO_NAME}.git"
    run_command(f"git remote add origin {remote_url}")
    
    # 6. 推送
    print("正在推送至 GitHub...")
    run_command("git push -u origin main")

if __name__ == "__main__":
    main()