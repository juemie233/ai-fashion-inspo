"""测试性能优化工具。"""

import asyncio
import time
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch
import pytest

from app.utils.performance import (
    async_performance_monitor,
    BatchProcessor,
    FileCache,
    process_files_concurrently,
    process_images_parallel,
    MemoryMonitor,
    optimize_memory_usage,
)

import logging


def _logger_text(mock_logger) -> str:
    """提取 mock logger 全部调用的消息文本，便于断言包含关系。"""
    parts = []
    for call in mock_logger.method_calls:
        args = call[1]
        if args and isinstance(args[0], str):
            parts.append(args[0])
    return "\n".join(parts)


class TestPerformanceMonitor:
    """测试性能监控装饰器。"""
    
    @pytest.mark.asyncio
    async def test_async_performance_monitor(self):
        """测试异步函数性能监控。"""
        @async_performance_monitor("测试函数")
        async def test_function():
            await asyncio.sleep(0.1)
            return "result"
        
        with patch("app.utils.performance.logger") as mock_logger:
            result = await test_function()
        assert result == "result"
        
        text = _logger_text(mock_logger)
        assert "测试函数" in text
        assert "执行耗时" in text
    
    def test_sync_performance_monitor(self):
        """测试同步函数性能监控。"""
        @async_performance_monitor("同步测试")
        def sync_function():
            time.sleep(0.1)
            return "sync_result"
        
        with patch("app.utils.performance.logger") as mock_logger:
            result = sync_function()
        assert result == "sync_result"
        
        text = _logger_text(mock_logger)
        assert "同步测试" in text
        assert "执行耗时" in text
    
    @pytest.mark.asyncio
    async def test_performance_monitor_with_exception(self):
        """测试异常情况下的性能监控。"""
        @async_performance_monitor("异常测试")
        async def failing_function():
            await asyncio.sleep(0.05)
            raise ValueError("测试错误")
        
        with patch("app.utils.performance.logger") as mock_logger:
            with pytest.raises(ValueError):
                await failing_function()
        
        # 检查是否记录了执行失败
        text = _logger_text(mock_logger)
        assert "异常测试" in text
        assert "执行失败" in text


class TestBatchProcessor:
    """测试批量处理器。"""
    
    @pytest.mark.asyncio
    async def test_batch_processor_success(self):
        """测试批量处理器成功执行。"""
        processor = BatchProcessor(max_concurrent=2)
        
        async def process_func(x):
            await asyncio.sleep(0.1)
            return x * 2
        
        tasks = [(i, process_func) for i in range(4)]
        results = await processor.process_batch(tasks, "测试批量")
        
        assert results == [0, 2, 4, 6]
    
    @pytest.mark.asyncio
    async def test_batch_processor_with_failure(self, caplog):
        """测试批量处理器处理失败情况。"""
        processor = BatchProcessor(max_concurrent=2)
        
        async def process_func(x):
            await asyncio.sleep(0.05)
            if x == 2:
                raise ValueError("处理失败")
            return x * 2
        
        tasks = [(i, process_func) for i in range(4)]
        results = await processor.process_batch(tasks, "测试批量失败")
        
        # 失败的任务应该返回None
        assert results[0] == 0
        assert results[1] == 2
        assert results[2] is None  # 失败的任务
        assert results[3] == 6
    
    @pytest.mark.asyncio
    async def test_batch_processor_concurrency_control(self):
        """测试批量处理器并发控制。"""
        processor = BatchProcessor(max_concurrent=2)
        
        concurrent_count = 0
        max_concurrent = 0
        
        async def process_func(x):
            nonlocal concurrent_count, max_concurrent
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
            await asyncio.sleep(0.1)
            concurrent_count -= 1
            return x
        
        tasks = [(i, process_func) for i in range(5)]
        await processor.process_batch(tasks, "并发控制测试")
        
        # 最大并发数不应该超过限制
        assert max_concurrent <= 2


class TestFileCache:
    """测试文件缓存。"""
    
    def test_cache_key_generation(self, tmp_path):
        """测试缓存键生成。"""
        cache = FileCache()
        
        # 创建测试文件
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")
        
        key1 = cache._get_cache_key(str(test_file), "operation1")
        key2 = cache._get_cache_key(str(test_file), "operation1")
        
        # 相同文件和操作应该生成相同的键
        assert key1 == key2
        
        # 不同操作应该生成不同的键
        key3 = cache._get_cache_key(str(test_file), "operation2")
        assert key1 != key3
    
    def test_cache_get_set_clear(self):
        """测试缓存的获取、设置和清空。"""
        cache = FileCache()
        
        # 设置缓存
        cache.set("file1", "operation1", "result1")
        cache.set("file2", "operation2", "result2")
        
        # 获取缓存
        assert cache.get("file1", "operation1") == "result1"
        assert cache.get("file2", "operation2") == "result2"
        assert cache.get("file1", "operation2") is None  # 不存在的缓存
        
        # 清空缓存
        cache.clear()
        assert cache.get("file1", "operation1") is None
        assert cache.get("file2", "operation2") is None


class TestFileProcessing:
    """测试文件处理功能。"""
    
    @pytest.mark.asyncio
    async def test_process_files_concurrently(self):
        """测试并发文件处理。"""
        processed_files = []
        
        async def mock_process_func(file_path):
            await asyncio.sleep(0.05)
            processed_files.append(file_path)
            return f"processed_{file_path}"
        
        file_paths = ["file1.txt", "file2.txt", "file3.txt"]
        # 显式关闭缓存：与其他用例的文件路径隔离，只验证并发处理本身
        results = await process_files_concurrently(
            file_paths, mock_process_func, max_concurrent=2, use_cache=False
        )
        
        # 检查所有文件都被处理了
        assert len(processed_files) == 3
        assert all(f"processed_{fp}" in results for fp in file_paths)
    
    @pytest.mark.asyncio
    async def test_process_files_with_cache(self):
        """测试带缓存的文件处理。"""
        processed_files = []
        
        async def mock_process_func(file_path):
            await asyncio.sleep(0.05)
            processed_files.append(file_path)
            return f"processed_{file_path}"
        
        file_paths = ["file1.txt", "file2.txt"]
        
        # 第一次处理
        results1 = await process_files_concurrently(
            file_paths, mock_process_func, use_cache=True
        )
        assert len(processed_files) == 2
        
        # 清空处理记录，但保留缓存
        processed_files.clear()
        
        # 第二次处理应该使用缓存
        results2 = await process_files_concurrently(
            file_paths, mock_process_func, use_cache=True
        )
        assert len(processed_files) == 0  # 因为使用了缓存
        assert results1 == results2
    
    @pytest.mark.asyncio
    async def test_process_images_parallel(self):
        """测试并行图片处理。"""
        processed_count = 0
        
        async def mock_process_func(image_path):
            nonlocal processed_count
            await asyncio.sleep(0.02)
            processed_count += 1
            return f"image_{image_path}"
        
        image_paths = [f"image_{i}.jpg" for i in range(10)]
        results = await process_images_parallel(
            image_paths, mock_process_func, batch_size=5
        )
        
        # 所有图片都应该被处理
        assert len(results) == 10
        assert processed_count == 10


class TestMemoryMonitor:
    """测试内存监控。"""
    
    @patch('psutil.Process')
    def test_get_memory_usage(self, mock_process):
        """测试获取内存使用情况。"""
        # 模拟psutil返回值
        mock_memory_info = Mock()
        mock_memory_info.rss = 1024 * 1024 * 100  # 100MB
        mock_memory_info.vms = 1024 * 1024 * 200  # 200MB
        mock_process.return_value.memory_info.return_value = mock_memory_info
        mock_process.return_value.memory_percent.return_value = 45.5
        
        usage = MemoryMonitor.get_memory_usage()
        
        assert usage["rss"] == 100.0
        assert usage["vms"] == 200.0
        assert usage["percent"] == 45.5
    
    @patch('app.utils.performance.MemoryMonitor.get_memory_usage')
    def test_log_memory_usage(self, mock_get_usage):
        """测试记录内存使用情况。"""
        mock_get_usage.return_value = {
            "rss": 100.0,
            "vms": 200.0,
            "percent": 45.5
        }
        
        with patch("app.utils.performance.logger") as mock_logger:
            MemoryMonitor.log_memory_usage("测试上下文")
        
        # 检查日志内容（用 mock 直接断言，规避环境相关的 caplog 差异）
        text = _logger_text(mock_logger)
        assert "内存使用 测试上下文" in text
        assert "RSS=100.0MB" in text
        assert "VMS=200.0MB" in text
        assert "45.5%" in text


class TestMemoryOptimization:
    """测试内存优化装饰器。"""
    
    @pytest.mark.asyncio
    @patch('app.utils.performance.MemoryMonitor.log_memory_usage')
    async def test_async_optimize_memory_usage(self, mock_log, caplog):
        """测试异步函数的内存优化装饰器。"""
        @optimize_memory_usage
        async def memory_intensive_function():
            await asyncio.sleep(0.1)
            return "result"
        
        result = await memory_intensive_function()
        assert result == "result"
        
        # 检查是否调用了内存记录（应该调用两次：执行前和执行后）
        assert mock_log.call_count >= 2
    
    @patch('app.utils.performance.MemoryMonitor.log_memory_usage')
    def test_sync_optimize_memory_usage(self, mock_log):
        """测试同步函数的内存优化装饰器。"""
        @optimize_memory_usage
        def sync_memory_function():
            time.sleep(0.1)
            return "sync_result"
        
        result = sync_memory_function()
        assert result == "sync_result"
        
        # 检查是否调用了内存记录
        assert mock_log.call_count >= 2
    
    @pytest.mark.asyncio
    @patch('app.utils.performance.MemoryMonitor.log_memory_usage')
    async def test_optimize_memory_with_exception(self, mock_log):
        """测试异常情况下的内存优化。"""
        @optimize_memory_usage
        async def failing_function():
            await asyncio.sleep(0.05)
            raise RuntimeError("内存不足")
        
        with pytest.raises(RuntimeError):
            await failing_function()
        
        # 检查是否记录了执行失败的内存情况
        assert any("执行失败" in call[0][0] for call in mock_log.call_args_list)


class TestPerformanceIntegration:
    """测试性能优化集成功能。"""
    
    @pytest.mark.asyncio
    async def test_end_to_end_processing(self):
        """测试端到端的处理流程。"""
        @async_performance_monitor("完整流程")
        @optimize_memory_usage
        async def complete_process():
            # 模拟完整的处理流程
            await asyncio.sleep(0.05)
            
            # 处理文件
            processor = BatchProcessor(max_concurrent=2)
            async def process(x):
                await asyncio.sleep(0.03)
                return x * 2
            
            tasks = [(i, process) for i in range(4)]
            results = await processor.process_batch(tasks, "集成测试")
            
            return results
        
        with patch("app.utils.performance.logger") as mock_logger:
            results = await complete_process()
        assert results == [0, 2, 4, 6]
        
        # 检查性能监控和内存监控的日志都生效了（用 mock 直接断言）
        text = _logger_text(mock_logger)
        assert "完整流程" in text
        assert "执行耗时" in text
        assert "内存使用" in text