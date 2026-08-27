#!/bin/bash
# 运行代码审查改进的测试脚本

echo "=========================================="
echo "开始运行代码审查改进测试"
echo "=========================================="

# 检查Python环境
echo "检查Python环境..."
python --version || { echo "Python未安装"; exit 1; }

# 检查必要的依赖
echo "检查测试依赖..."
python -c "import pytest" 2>/dev/null || { echo "需要安装pytest: pip install pytest"; exit 1; }
python -c "import pytest_asyncio" 2>/dev/null || { echo "需要安装pytest-asyncio: pip install pytest-asyncio"; exit 1; }
python -c "import psutil" 2>/dev/null || { echo "需要安装psutil: pip install psutil"; exit 1; }

echo "所有依赖检查通过！"
echo ""

# 运行异常处理测试
echo "=========================================="
echo "测试1: 异常处理测试"
echo "=========================================="
cd "$(dirname "$0")"
python -m pytest tests/test_exceptions.py -v --tb=short
if [ $? -eq 0 ]; then
    echo "✅ 异常处理测试通过"
else
    echo "❌ 异常处理测试失败"
    exit 1
fi
echo ""

# 运行性能优化测试
echo "=========================================="
echo "测试2: 性能优化测试"
echo "=========================================="
python -m pytest tests/test_performance.py -v --tb=short
if [ $? -eq 0 ]; then
    echo "✅ 性能优化测试通过"
else
    echo "❌ 性能优化测试失败"
    exit 1
fi
echo ""

# 运行配置管理测试
echo "=========================================="
echo "测试3: 配置管理测试"
echo "=========================================="
python -m pytest tests/test_config_constants.py -v --tb=short
if [ $? -eq 0 ]; then
    echo "✅ 配置管理测试通过"
else
    echo "❌ 配置管理测试失败"
    exit 1
fi
echo ""

echo "=========================================="
echo "🎉 所有测试通过！代码审查改进成功完成！"
echo "=========================================="
echo ""
echo "改进摘要："
echo "  ✅ 5.1 代码重复问题修复"
echo "  ✅ 5.2 配置管理优化"
echo "  ✅ 5.3 错误处理增强"
echo "  ✅ 5.4 性能优化"
echo "  ✅ 5.5 代码可读性改进"
echo "  ✅ 补充3个完整测试"
echo ""
echo "详细文档请查看: CODE_REVIEW_IMPROVEMENTS.md"
echo ""