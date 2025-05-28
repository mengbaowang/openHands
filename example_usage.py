#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
串口数据读取器使用示例
展示各种使用方法和场景
"""

import time
from datetime import datetime
from serial_data_reader import SerialDataReader


def example_1_basic_usage():
    """示例1：基本使用方法"""
    print("=== 示例1：基本使用方法 ===")
    
    # 创建串口读取器
    reader = SerialDataReader()
    
    # 扫描可用串口
    ports = reader.scan_ports()
    print(f"发现 {len(ports)} 个串口设备")
    
    if ports:
        # 使用第一个可用串口
        port_name = ports[0]['device']
        print(f"使用串口: {port_name}")
        
        # 连接串口
        if reader.connect(port_name):
            # 读取几条数据
            print("读取5条数据...")
            for i in range(5):
                data = reader.read_data()
                if data:
                    print(f"数据 {i+1}: {data}")
                time.sleep(1)
            
            # 断开连接
            reader.disconnect()
        else:
            print("连接失败")


def example_2_continuous_reading():
    """示例2：连续读取数据"""
    print("\n=== 示例2：连续读取数据 ===")
    
    def data_handler(data: str, timestamp: datetime):
        """数据处理函数"""
        print(f"[{timestamp.strftime('%H:%M:%S')}] 处理数据: {data}")
        
        # 这里可以添加数据处理逻辑
        if "ERROR" in data.upper():
            print("  ⚠️  检测到错误信息!")
        elif "GPS" in data.upper():
            print("  📍 GPS数据")
        elif "TEMP" in data.upper():
            print("  🌡️  温度数据")
    
    reader = SerialDataReader(baudrate=115200)
    ports = reader.scan_ports()
    
    if ports:
        port_name = ports[0]['device']
        
        if reader.connect(port_name):
            # 设置数据回调函数
            reader.set_data_callback(data_handler)
            
            # 开始连续读取
            reader.start_reading()
            
            print("连续读取10秒...")
            time.sleep(10)
            
            # 停止读取并保存数据
            reader.stop_reading()
            reader.save_data_to_csv("example_data.csv")
            reader.disconnect()


def example_3_data_communication():
    """示例3：双向数据通信"""
    print("\n=== 示例3：双向数据通信 ===")
    
    reader = SerialDataReader()
    ports = reader.scan_ports()
    
    if ports:
        port_name = ports[0]['device']
        
        if reader.connect(port_name):
            # 发送命令并等待响应
            commands = [
                "AT",           # AT命令测试
                "AT+VERSION",   # 查询版本
                "AT+STATUS",    # 查询状态
            ]
            
            for cmd in commands:
                print(f"发送命令: {cmd}")
                reader.write_data(cmd)
                
                # 等待响应
                time.sleep(1)
                response = reader.read_data()
                if response:
                    print(f"响应: {response}")
                else:
                    print("无响应")
                
                time.sleep(0.5)
            
            reader.disconnect()


def example_4_data_filtering():
    """示例4：数据过滤和处理"""
    print("\n=== 示例4：数据过滤和处理 ===")
    
    class DataProcessor:
        def __init__(self):
            self.gps_data = []
            self.sensor_data = []
            self.error_count = 0
        
        def process_data(self, data: str, timestamp: datetime):
            """数据处理回调"""
            # GPS数据过滤
            if data.startswith("$GP"):
                self.gps_data.append({
                    'timestamp': timestamp,
                    'data': data
                })
                print(f"GPS数据: {data}")
            
            # 传感器数据过滤
            elif "SENSOR" in data:
                self.sensor_data.append({
                    'timestamp': timestamp,
                    'data': data
                })
                print(f"传感器数据: {data}")
            
            # 错误检测
            elif "ERROR" in data.upper():
                self.error_count += 1
                print(f"⚠️  错误 #{self.error_count}: {data}")
            
            else:
                print(f"其他数据: {data}")
        
        def get_statistics(self):
            """获取统计信息"""
            return {
                'gps_count': len(self.gps_data),
                'sensor_count': len(self.sensor_data),
                'error_count': self.error_count
            }
    
    processor = DataProcessor()
    reader = SerialDataReader()
    ports = reader.scan_ports()
    
    if ports:
        port_name = ports[0]['device']
        
        if reader.connect(port_name):
            reader.set_data_callback(processor.process_data)
            reader.start_reading()
            
            print("数据过滤处理中...")
            time.sleep(15)
            
            reader.stop_reading()
            
            # 显示统计信息
            stats = processor.get_statistics()
            print(f"\n统计信息:")
            print(f"GPS数据: {stats['gps_count']} 条")
            print(f"传感器数据: {stats['sensor_count']} 条")
            print(f"错误信息: {stats['error_count']} 条")
            
            reader.disconnect()


def example_5_configuration_usage():
    """示例5：使用配置文件"""
    print("\n=== 示例5：使用配置文件 ===")
    
    import json
    
    # 读取配置文件
    try:
        with open('serial_config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        settings = config['default_settings']
        
        # 使用配置创建读取器
        reader = SerialDataReader(
            baudrate=settings['baudrate'],
            timeout=settings['timeout'],
            encoding=settings['encoding']
        )
        
        print(f"使用配置: 波特率={settings['baudrate']}, 超时={settings['timeout']}")
        
        ports = reader.scan_ports()
        if ports:
            port_name = ports[0]['device']
            
            if reader.connect(port_name):
                print("配置加载成功，开始读取数据...")
                
                # 简单读取几条数据
                for i in range(3):
                    data = reader.read_data()
                    if data:
                        print(f"数据: {data}")
                    time.sleep(1)
                
                reader.disconnect()
    
    except FileNotFoundError:
        print("配置文件 serial_config.json 未找到")
    except Exception as e:
        print(f"配置加载错误: {e}")


def main():
    """主函数 - 运行所有示例"""
    print("串口数据读取器使用示例")
    print("=" * 50)
    
    # 注意：这些示例需要真实的串口设备
    # 如果没有串口设备，请使用 virtual_serial_demo.py
    
    try:
        example_1_basic_usage()
        example_2_continuous_reading()
        example_3_data_communication()
        example_4_data_filtering()
        example_5_configuration_usage()
        
    except Exception as e:
        print(f"示例运行错误: {e}")
        print("提示：如果没有真实串口设备，请运行 virtual_serial_demo.py")


if __name__ == "__main__":
    main()