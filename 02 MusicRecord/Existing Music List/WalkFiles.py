import os
import sys
import time


def get_target_directory():
    """获取并验证目标目录"""
    # 检查是否通过命令行传递了参数
    if len(sys.argv) < 2:
        print("错误: 请提供目标文件夹路径作为参数。")
        print(f"用法: python {sys.argv[0]} <目标文件夹路径>")
        sys.exit(1)

    target_dir = sys.argv[1]

    # 将路径转换为绝对路径，并检查是否存在
    abs_target_dir = os.path.abspath(target_dir)
    if not os.path.exists(abs_target_dir):
        print(f"错误: 路径不存在 -> {abs_target_dir}")
        sys.exit(1)

    if not os.path.isdir(abs_target_dir):
        print(f"错误: 指定的路径不是一个文件夹 -> {abs_target_dir}")
        sys.exit(1)

    return abs_target_dir


def main():
    # 1. 获取并验证目标路径
    target_dir = get_target_directory()
    print(f"成功定位目标文件夹: {target_dir}")

    # 2. 用户交互选择模式
    print("\n请选择输出格式：")
    print("  [A] - 绝对路径列表 (例如: /home/user/dir/file.mp3)")
    print("  [B] - 相对/半绝对路径列表 (例如: subfolder/file.mp3)")
    print("  [C] - 纯文件名列表 (例如: file.mp3)")

    choice = input("请输入你的选择 (A/B/C): ").strip().upper()

    if choice not in ["A", "B", "C"]:
        print("错误: 无效的选择，脚本退出。")
        sys.exit(1)

    # 3. 根据选择准备文件名
    timestamp = int(time.time())
    if choice == "A":
        output_filename = f"abs_path_list_{timestamp}.txt"
    elif choice == "B":
        output_filename = f"halp_path_list_{timestamp}.txt"
    else:
        output_filename = f"file_only_list_{timestamp}.txt"

    print(f"\n正在遍历文件夹并写入: {output_filename} ...")

    # 4. 遍历文件夹并写入内容
    file_count = 0
    with open(output_filename, "w", encoding="utf-8") as f:
        for root, dirs, files in os.walk(target_dir):
            for file in files:
                full_path = os.path.join(root, file)

                # 根据不同选项处理路径
                if choice == "A":
                    content = full_path
                elif choice == "B":
                    # os.path.relpath 会计算出相对于目标文件夹的路径
                    content = os.path.relpath(full_path, target_dir)
                else:
                    content = file

                # 写入文件
                f.write(content + "\n")
                file_count += 1

    print(f"处理完成！成功记录了 {file_count} 个文件。")


if __name__ == "__main__":
    main()