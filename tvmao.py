import datetime
import requests
import json
import gzip
from dateutil import tz
from typing import List, Dict, Any, Optional

# ==================== 配置部分 ====================
with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

CHANNELS = config["channels"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://lighttv.tvmao.com/",
    "Origin": "https://lighttv.tvmao.com"
}

# ==================== 核心函数 ====================
def fetch_channel_epg(channel_name: str, channel_id: str, target_date: datetime.date) -> Dict[str, Any]:
    """
    获取单个频道在指定日期的节目表
    
    Args:
        channel_name: 频道名称（用于显示）
        channel_id: 频道ID（用于API请求）
        target_date: 目标日期
        
    Returns:
        包含节目列表和状态信息的字典
    """
    today = datetime.datetime.now().date()
    delta_days = (target_date - today).days
    
    # API的day参数：1=今天，2=明天，3=后天...
    day_num = delta_days + 1
    
    # 检查日期是否在API支持范围内（1-5）
    if day_num < 1 or day_num > 5:
        return {
            "success": False,
            "channel_name": channel_name,
            "channel_id": channel_id,
            "date": target_date,
            "epgs": [],
            "error": f"日期超出API范围，只支持今天到未来4天（day_num={day_num}）"
        }
    
    # 构建API URL
    url = f"https://lighttv.tvmao.com/qa/qachannelschedule"
    params = {
        "epgCode": channel_id,
        "op": "getProgramByChnid",
        "epgName": "",
        "isNew": "on",
        "day": day_num
    }
    
    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        # 验证数据结构
        if not isinstance(data, list) or len(data) < 3:
            return {
                "success": False,
                "channel_name": channel_name,
                "channel_id": channel_id,
                "date": target_date,
                "epgs": [],
                "error": f"API返回数据结构异常: {data[:100] if data else '空数据'}"
            }
        
        # 提取节目数据
        program_data = data[2]
        if not isinstance(program_data, dict) or "pro" not in program_data:
            return {
                "success": False,
                "channel_name": channel_name,
                "channel_id": channel_id,
                "date": target_date,
                "epgs": [],
                "error": "API返回数据中没有节目信息"
            }
        
        programs = program_data["pro"]
        epg_list = []
        
        # 处理每个节目
        for i, program in enumerate(programs):
            try:
                # 解析开始时间
                time_str = program.get("time", "00:00")
                hour, minute = map(int, time_str.split(":"))
                start_time = datetime.datetime.combine(target_date, datetime.time(hour, minute))
                
                # 计算结束时间
                if i < len(programs) - 1:
                    # 下一个节目的开始时间作为当前节目的结束时间
                    next_time_str = programs[i + 1]["time"]
                    next_hour, next_minute = map(int, next_time_str.split(":"))
                    
                    # 处理跨天情况（如23:30 -> 00:30）
                    if next_hour < hour and hour >= 23:
                        # 跨天到第二天
                        next_date = target_date + datetime.timedelta(days=1)
                        end_time = datetime.datetime.combine(next_date, datetime.time(next_hour, next_minute))
                    else:
                        end_time = datetime.datetime.combine(target_date, datetime.time(next_hour, next_minute))
                else:
                    # 最后一个节目：根据时间合理设置结束时间
                    if hour >= 23:
                        # 接近午夜，设置为第二天00:30
                        next_date = target_date + datetime.timedelta(days=1)
                        end_time = datetime.datetime.combine(next_date, datetime.time(0, 30))
                    elif hour >= 22:
                        # 晚上节目，设为1小时后
                        end_time = start_time + datetime.timedelta(hours=1)
                    else:
                        # 其他时间设为30分钟后
                        end_time = start_time + datetime.timedelta(minutes=30)
                
                epg_list.append({
                    "channel_name": channel_name,
                    "channel_id": channel_id,
                    "start": start_time,
                    "end": end_time,
                    "title": program.get("name", "未知节目"),
                    "typeid": program.get("typeid", 0),
                    "status": program.get("status", -1),
                    "pid": program.get("pid", ""),
                    "date": target_date
                })
                
            except Exception as e:
                print(f"  解析节目 {i} 时出错: {e}, program: {program}")
                continue
        
        return {
            "success": True,
            "channel_name": channel_name,
            "channel_id": channel_id,
            "date": target_date,
            "epgs": epg_list,
            "count": len(epg_list),
            "error": None
        }
        
    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "channel_name": channel_name,
            "channel_id": channel_id,
            "date": target_date,
            "epgs": [],
            "error": f"网络请求失败: {str(e)}"
        }
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "channel_name": channel_name,
            "channel_id": channel_id,
            "date": target_date,
            "epgs": [],
            "error": f"JSON解析失败: {str(e)}"
        }
    except Exception as e:
        return {
            "success": False,
            "channel_name": channel_name,
            "channel_id": channel_id,
            "date": target_date,
            "epgs": [],
            "error": f"未知错误: {str(e)}"
        }

# ==================== XML生成函数 ====================
def generate_xmltv(epg_data: List[Dict[str, Any]], output_file: str = "tvmao.xml") -> None:
    """
    生成XMLTV格式的EPG文件
    
    Args:
        epg_data: 所有节目的列表
        output_file: 输出文件名
    """
    tz_shanghai = tz.gettz("Asia/Shanghai")
    
    # 收集所有唯一的频道
    channels = {}
    for epg in epg_data:
        channel_id = epg["channel_id"]
        if channel_id not in channels:
            channels[channel_id] = epg["channel_name"]
    
    # 生成XML
    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
        '<tv source-info-name="TVMAO" source-info-url="https://www.tvmao.com" generator-info-name="TVMAO-EPG" generator-info-url="">'
    ]
    
    # 添加频道定义
    for channel_id, channel_name in channels.items():
        xml_lines.extend([
            f'  <channel id="{channel_id}">',
            f'    <display-name lang="zh">{channel_name}</display-name>',
            f'  </channel>'
        ])
    
    # 添加节目信息
    for epg in epg_data:
        # 转换时区
        start_time = epg["start"].replace(tzinfo=tz_shanghai)
        end_time = epg["end"].replace(tzinfo=tz_shanghai)
        
        # 格式化时间
        start_str = start_time.strftime("%Y%m%d%H%M%S %z")
        end_str = end_time.strftime("%Y%m%d%H%M%S %z")
        
        # XML转义
        title = epg["title"]
        for old, new in [
            ("&", "&amp;"),
            ("<", "&lt;"),
            (">", "&gt;"),
            ("'", "&apos;"),
            ('"', "&quot;")
        ]:
            title = title.replace(old, new)
        
        # 构建programme元素
        xml_lines.extend([
            f'  <programme start="{start_str}" stop="{end_str}" channel="{epg["channel_id"]}">',
            f'    <title lang="zh">{title}</title>'
        ])
        
        # 可以添加更多信息，如节目描述等（如果API提供）
        if epg.get("typeid"):
            xml_lines.append(f'    <category lang="zh">类型{epg["typeid"]}</category>')
        
        xml_lines.append('  </programme>')
    
    xml_lines.append('</tv>')
    
    # 写入文件
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(xml_lines))
    
    print(f"✓ XMLTV文件已生成: {output_file}")
    
    # 生成压缩文件
    with open(output_file, "rb") as f_in:
        with gzip.open(f"{output_file}.gz", "wb") as f_out:
            f_out.writelines(f_in)
    
    print(f"✓ 压缩文件已生成: {output_file}.gz")

# ==================== 主程序 ====================
def main():
    """主程序"""
    print("=" * 60)
    print("TVMAO EPG 采集工具")
    print("=" * 60)
    
    # 获取要采集的日期（今天、明天、后天）
    today = datetime.datetime.now().date()
    target_dates = [
        today,
        today + datetime.timedelta(days=1),
        today + datetime.timedelta(days=2)
    ]
    
    print(f"采集日期范围: {target_dates[0]} 到 {target_dates[-1]}")
    print(f"频道数量: {len(CHANNELS)}")
    print("-" * 60)
    
    all_epgs = []
    success_count = 0
    fail_count = 0
    
    # 遍历所有频道和日期
    total_tasks = len(CHANNELS) * len(target_dates)
    current_task = 0
    
    for channel_name, channel_info in CHANNELS.items():
        channel_id = channel_info[1]  # 第二个元素是channel_id
        
        for target_date in target_dates:
            current_task += 1
            print(f"[{current_task}/{total_tasks}] 正在采集: {channel_name} ({target_date})", end="", flush=True)
            
            result = fetch_channel_epg(channel_name, channel_id, target_date)
            
            if result["success"]:
                all_epgs.extend(result["epgs"])
                success_count += 1
                print(f" ✓ 获取 {result['count']} 个节目")
            else:
                fail_count += 1
                print(f" ✗ 失败: {result['error'][:50]}...")
            
            # 避免请求过快，适当延迟
            import time
            time.sleep(0.5)
    
    print("-" * 60)
    print(f"采集完成!")
    print(f"成功: {success_count}, 失败: {fail_count}")
    print(f"总共获取节目数: {len(all_epgs)}")
    
    if all_epgs:
        # 按频道和开始时间排序
        all_epgs.sort(key=lambda x: (x["channel_id"], x["start"]))
        
        # 生成XML文件
        generate_xmltv(all_epgs)
        
        # 输出统计信息
        print("\n频道节目统计:")
        print("-" * 60)
        
        channel_stats = {}
        for epg in all_epgs:
            channel_id = epg["channel_id"]
            if channel_id not in channel_stats:
                channel_stats[channel_id] = 0
            channel_stats[channel_id] += 1
        
        for channel_id, count in sorted(channel_stats.items(), key=lambda x: x[1], reverse=True):
            channel_name = next((name for name, info in CHANNELS.items() if info[1] == channel_id), channel_id)
            print(f"  {channel_name:20} ({channel_id:10}): {count:3} 个节目")
        
        # 输出时间范围
        if all_epgs:
            earliest = min(epg["start"] for epg in all_epgs)
            latest = max(epg["end"] for epg in all_epgs)
            print(f"\n时间范围: {earliest} 到 {latest}")
    else:
        print("警告: 没有获取到任何节目数据!")

# ==================== 单频道测试函数 ====================
def test_single_channel(channel_name: str = None):
    """
    测试单个频道的EPG获取（用于调试）
    
    Args:
        channel_name: 要测试的频道名称，如果为None则使用第一个频道
    """
    print("=" * 60)
    print("单频道测试模式")
    print("=" * 60)
    
    if channel_name:
        channel_info = CHANNELS.get(channel_name)
        if not channel_info:
            print(f"错误: 找不到频道 '{channel_name}'")
            available = list(CHANNELS.keys())[:10]
            print(f"可用的频道: {', '.join(available)}...")
            return
    else:
        # 使用第一个频道进行测试
        channel_name = list(CHANNELS.keys())[0]
        channel_info = CHANNELS[channel_name]
    
    channel_id = channel_info[1]
    today = datetime.datetime.now().date()
    
    print(f"测试频道: {channel_name} (ID: {channel_id})")
    print(f"测试日期: {today}")
    print("-" * 60)
    
    result = fetch_channel_epg(channel_name, channel_id, today)
    
    if result["success"]:
        print(f"✓ 成功获取 {result['count']} 个节目")
        print("\n前10个节目:")
        print("-" * 60)
        
        for i, epg in enumerate(result["epgs"][:10]):
            start_time = epg["start"].strftime("%H:%M")
            end_time = epg["end"].strftime("%H:%M")
            print(f"  {start_time}-{end_time}: {epg['title']}")
        
        if result["count"] > 10:
            print(f"  ... 还有 {result['count'] - 10} 个节目未显示")
        
        # 显示API原始数据样例
        print("\nAPI数据样例:")
        print("-" * 60)
        url = f"https://lighttv.tvmao.com/qa/qachannelschedule?epgCode={channel_id}&op=getProgramByChnid&epgName=&isNew=on&day=1"
        print(f"API URL: {url}")
    else:
        print(f"✗ 失败: {result['error']}")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        # 测试模式
        if len(sys.argv) > 2:
            test_single_channel(sys.argv[2])
        else:
            test_single_channel()
    else:
        # 正常模式
        main()