#!/bin/sh
# 麦门顶饿算账 · 苹果电脑启动脚本
# 用法：终端里执行  sh run.sh
# 想双击运行，把文件名改成 run.command 即可（macOS 认这个后缀）

set -e
cd "$(dirname "$0")"

echo ""
echo "  麦门顶饿算账 · 麦当劳官方数据实时算账"
echo "  ----------------------------------------"
echo ""

printf "  城市（直接回车 = 周口市）："
read city
[ -z "$city" ] && city="周口市"

printf "  门店关键词（直接回车 = 自动找最近的门店）："
read kw

printf "  预算几块钱（直接回车 = 35）："
read bud
[ -z "$bud" ] && bud="35"

printf "  热量上限 kcal（直接回车 = 800，填 0 不限）："
read kc
[ -z "$kc" ] && kc="800"

printf "  只看一句话结论，方便发微信？（y / 直接回车 = 不要）："
read one

PY=python3
if ! command -v python3 >/dev/null 2>&1; then
    PY=python
fi
if ! command -v "$PY" >/dev/null 2>&1; then
    echo ""
    echo "  没找到 Python。macOS 一般自带 python3，如果提示缺失，"
    echo "  在终端执行  xcode-select --install  装一下命令行工具即可。"
    echo ""
    printf "  按回车关闭"
    read _
    exit 1
fi

echo ""
echo "  正在查麦当劳官方接口..."
echo ""

if [ -z "$kw" ]; then
    set -- --city "$city" --budget "$bud" --kcal "$kc"
else
    set -- --city "$city" --keyword "$kw" --budget "$bud" --kcal "$kc"
fi

case "$one" in
    y|Y) "$PY" mcd_solver.py "$@" --oneline ;;
    *)   "$PY" mcd_solver.py "$@" ;;
esac

echo ""
echo "  ----------------------------------------"
printf "  按回车关闭"
read _
