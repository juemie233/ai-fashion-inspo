"""性能优化工具：提供异步文件处理、缓存和性能监控。"""

import asyncio
import logging
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)


def async_performance_monitor(func_name: str = None):
    """性能监控装饰器：记录函数执行时间。
    
    Args:
        func_name: 函数名称，默认使用函数名
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                execution_time = time.time() - start_time
                logger.info(f"{func_name or func.__name__} 执行耗时: {execution_time:.3f}s")
                return result
            except Exception as e:
                execution_time = time.time() - start_time
                logger.error(f"{func_name or func.__name__} 执行失败，耗时: {execution_time:.3f}s, 错误: {e}")
                raise
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                execution_time = time.time() - start_time
                logger.info(f"{func_name or func.__name__} 执行耗时: {execution_time:.3f}s")
                return result
            except Exception as e:
                execution_time = time.time() - start_time
                logger.error(f"{func_name or func.__name__} 执行失败，耗时: {execution_time:.3f}s, 错误: {e}")
                raise
        
        # 判断是否是异步函数
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


class BatchProcessor:
    """批量处理器：并行处理任务，控制并发数。"""
    
    def __init__(self, max_concurrent: int = None):
        self.max_concurrent = max_concurrent or settings.config_constants.scraper_max_concurrent
        self.semaphore = asyncio.Semaphore(self.max_concurrent)
    
    async def process_batch(
        self, 
        tasks: List[Tuple[Any, Callable]], 
        task_name: str = "批量任务"
    ) -> List[Any]:
        """并行处理批量任务。
        
        Args:
            tasks: 任务列表，每个元素为 (参数, 函数) 元组；
                   参数可为单值或元组/列表（自动归一化为位置参数）
            task_name: 任务名称，用于日志
            
        Returns:
            结果列表，与输入顺序一致
        """
        results = [None] * len(tasks)
        
        async def process_task(index, args, func):
            async with self.semaphore:
                try:
                    if not isinstance(args, (tuple, list)):
                        args = (args,)
                    outcome = func(*args)
                    # 兼容同步函数（含缓存包装 lambda）与协程函数
                    result = await outcome if asyncio.iscoroutine(outcome) else outcome
                    results[index] = result
                    return result
                except Exception as e:
                    logger.error(f"任务 {index} 失败: {e}")
                    results[index] = None
                    return None
        
        # 创建所有任务
        task_coroutines = [
            process_task(i, args, func) 
            for i, (args, func) in enumerate(tasks)
        ]
        
        # 并发执行所有任务
        await asyncio.gather(*task_coroutines, return_exceptions=True)
        
        # 统计结果
        success_count = sum(1 for r in results if r is not None)
        logger.info(f"{task_name} 完成: {success_count}/{len(tasks)} 成功")
        
        return results


class FileCache:
    """简单内存缓存：按文件路径+操作名缓存结果，避免重复处理相同文件。

    纯内存实现，实例化时不触碰磁盘；缓存键携带文件 mtime+size，
    文件变更后自动失效。文件不存在时使用固定标记（调用方可自行保证语义）。
    """
    
    def __init__(self) -> None:
        self._cache: Dict[str, Any] = {}
    
    def _get_cache_key(self, file_path: str, operation: str) -> str:
        """生成缓存键；文件不存在时不抛错，退化为「无文件」标记。"""
        try:
            stat = Path(file_path).stat()
            marker = f"{stat.st_mtime}:{stat.st_size}"
        except OSError:
            marker = "no-file"
        return f"{file_path}:{operation}:{marker}"
    
    def get(self, file_path: str, operation: str) -> Any:
        """获取缓存结果。operation 建议传 process_func.__qualname__（跨作用域唯一）。"""
        key = self._get_cache_key(file_path, operation)
        return self._cache.get(key)
    
    def set(self, file_path: str, operation: str, result: Any) -> None:
        """设置缓存结果。"""
        key = self._get_cache_key(file_path, operation)
        self._cache[key] = result
    
    def clear(self) -> None:
        """清空缓存。"""
        self._cache.clear()


# 全局缓存实例
file_cache = FileCache()


@async_performance_monitor("batch_file_processing")
async def process_files_concurrently(
    file_paths: List[str],
    process_func: Callable,
    max_concurrent: int = None,
    use_cache: bool = True
) -> List[Any]:
    """并发处理文件列表。
    
    Args:
        file_paths: 文件路径列表
        process_func: 处理函数，接受文件路径作为参数
        max_concurrent: 最大并发数
        use_cache: 是否使用缓存
        
    Returns:
        处理结果列表
    """
    processor = BatchProcessor(max_concurrent)
    
    tasks = []
    # 缓存操作名用 __qualname__：避免不同测试/模块内同名嵌套函数互串缓存
    op_name = getattr(process_func, "__qualname__", process_func.__name__)
    for file_path in file_paths:
        # 检查缓存；用默认参数固化 cached_result，避免闭包晚绑定读到末次循环值
        if use_cache:
            cached_result = file_cache.get(file_path, op_name)
            if cached_result is not None:
                logger.debug(f"使用缓存结果: {file_path}")
                tasks.append(([file_path], lambda p, r=cached_result: r))
                continue
        
        tasks.append(([file_path], process_func))
    
    results = await processor.process_batch(tasks, "文件处理")
    
    # 更新缓存
    if use_cache:
        for i, file_path in enumerate(file_paths):
            if results[i] is not None:
                file_cache.set(file_path, op_name, results[i])
    
    return results


@async_performance_monitor("parallel_image_processing")
async def process_images_parallel(
    image_paths: List[str],
    process_func: Callable,
    batch_size: int = 10
) -> List[Any]:
    """并行处理图片，分批执行避免内存溢出。
    
    Args:
        image_paths: 图片路径列表
        process_func: 处理函数
        batch_size: 每批处理数量
        
    Returns:
        处理结果列表
    """
    all_results = []
    
    # 分批处理
    for i in range(0, len(image_paths), batch_size):
        batch = image_paths[i:i + batch_size]
        logger.info(f"处理第 {i//batch_size + 1} 批，共 {len(batch)} 张图片")
        
        batch_results = await process_files_concurrently(
            batch, 
            process_func, 
            max_concurrent=2  # 图片处理是CPU密集型，限制并发数
        )
        
        all_results.extend(batch_results)
    
    return all_results


class MemoryMonitor:
    """内存监控：监控内存使用情况。"""
    
    @staticmethod
    def get_memory_usage() -> Dict[str, float]:
        """获取当前内存使用情况（MB）。"""
        import psutil
        process = psutil.Process()
        memory_info = process.memory_info()
        
        return {
            "rss": memory_info.rss / 1024 / 1024,  # 物理内存
            "vms": memory_info.vms / 1024 / 1024,  # 虚拟内存
            "percent": process.memory_percent()  # 内存使用百分比
        }
    
    @staticmethod
    def log_memory_usage(context: str = "") -> None:
        """记录内存使用情况。"""
        usage = MemoryMonitor.get_memory_usage()
        logger.info(f"内存使用 {context}: RSS={usage['rss']:.1f}MB, VMS={usage['vms']:.1f}MB, {usage['percent']:.1f}%")


def optimize_memory_usage(func: Callable) -> Callable:
    """内存使用优化装饰器：在函数执行前后记录内存使用情况。"""
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        MemoryMonitor.log_memory_usage(f"执行前 {func.__name__}")
        try:
            result = await func(*args, **kwargs)
            MemoryMonitor.log_memory_usage(f"执行后 {func.__name__}")
            return result
        except Exception as e:
            MemoryMonitor.log_memory_usage(f"执行失败 {func.__name__}")
            raise
    
    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        MemoryMonitor.log_memory_usage(f"执行前 {func.__name__}")
        try:
            result = func(*args, **kwargs)
            MemoryMonitor.log_memory_usage(f"执行后 {func.__name__}")
            return result
        except Exception as e:
            MemoryMonitor.log_memory_usage(f"执行失败 {func.__name__}")
            raise
    
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper