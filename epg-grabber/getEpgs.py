import os
import gzip
import xml.etree.ElementTree as ET
import requests
import logging
from copy import deepcopy
import datetime
import pytz
import re
from xml.sax.saxutils import escape
from collections import defaultdict

# 配置参数
config_file = os.path.join(os.path.dirname(__file__), 'config.txt')
output_file_gz = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'e.xml.gz')
TIMEZONE = pytz.timezone('Asia/Shanghai')

def load_config_and_alias(config_file):
    """从合并的配置文件中加载频道名称和别名映射"""
    config_names = set()
    alias_mapping = {}
    
    try:
        with open(config_file, 'r', encoding='utf-8') as file:
            for line in file:
                cleaned_line = line.strip()
                if not cleaned_line or cleaned_line.startswith('#'):
                    continue
                
                # 按逗号分割
                parts = [p.strip() for p in cleaned_line.split(',') if p.strip()]
                
                if len(parts) == 0:
                    continue
                
                # 第一个总是标准名称
                standard_name = parts[0]
                
                # 添加到配置名称集合
                config_names.add(standard_name)
                
                # 如果有别名，添加到映射表
                if len(parts) > 1:
                    for alias in parts[1:]:
                        alias_mapping[alias] = standard_name
        
        logging.info(f"Loaded {len(config_names)} channels and {len(alias_mapping)} alias mappings from {config_file}")
        return config_names, alias_mapping
        
    except Exception as e:
        logging.error(f"Failed to load config: {e}")
        return set(), {}

def map_channel(display_name, config_names, alias_mapping):
    """频道匹配核心逻辑 - 保持你原来的逻辑"""
    # 1. 直接匹配config.txt
    if display_name in config_names:
        logging.info(f"直接匹配成功: {display_name}")
        return display_name
    
    # 2. 通过映射表匹配
    mapped_name = alias_mapping.get(display_name)
    if mapped_name:
        if mapped_name in config_names:
            logging.info(f"别名映射成功: {display_name} -> {mapped_name}")
            return mapped_name
        else:
            logging.warning(f"映射目标不在配置中: {mapped_name} (来自 {display_name})")
    else:
        logging.debug(f"未找到映射: {display_name}")
    
    return None

def parse_epg_time(time_str):
    """解析EPG时间并转换为本地时区"""
    try:
        if not time_str or len(time_str) < 14:
            return None
        dt = datetime.datetime.strptime(time_str[:14], "%Y%m%d%H%M%S")
        if time_str.endswith('Z'):
            dt = pytz.utc.localize(dt)
        else:
            dt = TIMEZONE.localize(dt)
        return dt
    except Exception as e:
        return None

def normalize_title(title):
    """标准化标题，但保留集数信息"""
    if not title:
        return ""
    
    # 提取并保留集数信息
    episode_info = ""
    episode_patterns = [
        r'(第[一二三四五六七八九十零百千万0-9]+[集期部回])',  # 中文集数
        r'([上下]集)',  # 上下集
        r'(第?\d+[集期部回])',  # 数字集数
        r'(\(第?\d+集\))',  # 括号内的集数
        r'(【第?\d+集】)',  # 方括号内的集数
    ]
    
    normalized = title.strip()
    
    # 先提取集数信息
    for pattern in episode_patterns:
        match = re.search(pattern, normalized)
        if match:
            episode_info = match.group(1)
            break
    
    # 移除常见的年份和编号模式，但保留集数
    patterns_to_remove = [
        r'\s*\d{4}[-_]\d+$',           # 如 "活力·源2025-226", "味道-2025-31"
        r'[-_]\d{4}[-_]\d+$',          # 如 "特别呈现2024-352"
        r'[-_]\d+$',                   # 如 "精彩多看点-5"
        r'\s*\d+$',                    # 如 "世界地理50"
        r'[-_]\d{4}年',                # 年份
        r'字幕板[-_]\d{4}[-_]\d+$',    # 如 "世界地理频道字幕板-2023-2"
        r'-\d{4}-\d+$',                # 如 "-2025-31"
    ]
    
    for pattern in patterns_to_remove:
        normalized = re.sub(pattern, '', normalized)
    
    # 如果提取到了集数信息，加回到标题中
    if episode_info:
        # 确保集数信息在标题末尾
        normalized = re.sub(episode_patterns[0], '', normalized)  # 先移除可能已经存在的集数
        normalized = normalized.strip()
        # 在集数前加空格（如果还没有的话）
        if normalized and not normalized.endswith(' '):
            normalized += ' '
        normalized += episode_info
    
    # 清理多余空格
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    
    return normalized if normalized else title.strip()

def normalize_time(time_str, interval_minutes=5):
    """时间归一化：将时间对齐到指定的分钟间隔"""
    if not time_str or len(time_str) < 14:
        return time_str
    
    try:
        dt = datetime.datetime.strptime(time_str[:14], "%Y%m%d%H%M%S")
        # 对齐到指定的分钟间隔
        minute = dt.minute
        aligned_minute = (minute // interval_minutes) * interval_minutes
        dt = dt.replace(minute=aligned_minute, second=0)
        
        # 保持原始时区信息
        timezone_part = time_str[14:] if len(time_str) > 14 else " +0800"
        return dt.strftime("%Y%m%d%H%M%S") + timezone_part
    except Exception as e:
        return time_str

def get_program_quality(program):
    """评估节目质量，返回分数"""
    score = 0
    
    # 检查是否有描述
    desc_elem = program.find('desc')
    if desc_elem is not None:
        desc_text = desc_elem.text
        if desc_text is not None and desc_text.strip():
            desc_text = desc_text.strip()
            # 长描述得分更高
            if len(desc_text) > 20:
                score += 3
            else:
                score += 2
    
    # 检查是否有语言属性
    title_elem = program.find('title')
    if title_elem is not None:
        lang_attr = title_elem.get('lang', '')
        if lang_attr:
            score += 1
        
        # 检查标题是否详细
        title_text = title_elem.text if title_elem.text is not None else ""
        # 有年份/编号信息加分
        if re.search(r'\d{4}[-_]\d+', title_text):
            score += 1
    
    # 检查其他信息
    if program.find('category') is not None:
        score += 1
    if program.find('sub-title') is not None:
        score += 1
    
    return score

def programs_overlap(prog1, prog2, threshold_minutes=5):
    """检查两个节目是否时间重叠"""
    def get_datetime(time_str):
        parsed = parse_epg_time(time_str)
        return parsed if parsed else None
    
    start1 = get_datetime(prog1.get('start', ''))
    stop1 = get_datetime(prog1.get('stop', ''))
    start2 = get_datetime(prog2.get('start', ''))
    stop2 = get_datetime(prog2.get('stop', ''))
    
    if not all([start1, stop1, start2, stop2]):
        return False
    
    # 计算重叠时间（分钟）
    overlap_start = max(start1, start2)
    overlap_end = min(stop1, stop2)
    
    if overlap_start < overlap_end:
        overlap_minutes = (overlap_end - overlap_start).total_seconds() / 60
        return overlap_minutes >= threshold_minutes
    
    return False

def process_programme(programme, mapped_id):
    """处理单个节目节点"""
    new_prog = deepcopy(programme)
    
    # 获取原始开始时间
    start_time_str = programme.get('start', '')
    start_time = parse_epg_time(start_time_str)
    
    if not start_time:
        return None
    
    # 清理并设置语言属性
    for elem in new_prog.findall('title'):
        if not elem.get('lang'):
            elem.set('lang', 'zh')
        if elem.text is not None:
            elem.text = escape(elem.text.strip())
    
    for elem in new_prog.findall('desc'):
        if not elem.get('lang'):
            elem.set('lang', 'zh')
        if elem.text is not None:
            elem.text = escape(elem.text.strip())
    
    # 设置频道
    new_prog.set('channel', mapped_id)
    
    # 处理时间信息
    if start_time_str:
        # 保持原始时间格式，只更新时区部分
        if ' +' not in start_time_str and ' -' not in start_time_str and not start_time_str.endswith('Z'):
            new_prog.set('start', start_time_str[:14] + " +0800")
    
    # 处理结束时间
    stop_attr = programme.get('stop')
    if stop_attr:
        stop_time = parse_epg_time(stop_attr)
        if stop_time and stop_time > start_time:
            if ' +' not in stop_attr and ' -' not in stop_attr and not stop_attr.endswith('Z'):
                new_prog.set('stop', stop_attr[:14] + " +0800")
    
    return new_prog

def deduplicate_programs(programs_list):
    """去重节目列表，应用完整的去重策略"""
    if not programs_list:
        return []
    
    # 第一步：按时间归一化分组
    time_groups = defaultdict(list)
    for prog in programs_list:
        # 获取归一化时间键（对齐到5分钟）
        start_time = prog.get('start', '')
        normalized_start = normalize_time(start_time, 5)
        
        if not normalized_start:
            continue
        
        # 获取归一化标题（保留集数信息）
        title_elem = prog.find('title')
        title_text = title_elem.text if title_elem is not None and title_elem.text is not None else ''
        normalized_title = normalize_title(title_text)
        
        # 创建分组键（频道 + 时间 + 标题）
        channel_id = prog.get('channel', '')
        time_key = normalized_start[:12]  # 精确到分钟
        group_key = f"{channel_id}|{time_key}|{normalized_title}"
        
        time_groups[group_key].append(prog)
    
    # 第二步：在每个分组中选择质量最高的节目
    selected_programs = []
    for group_key, group_programs in time_groups.items():
        if len(group_programs) == 1:
            selected_programs.append(group_programs[0])
        else:
            # 按质量评分排序
            scored_programs = []
            for prog in group_programs:
                score = get_program_quality(prog)
                scored_programs.append((score, prog))
            
            # 按分数降序排序
            scored_programs.sort(key=lambda x: x[0], reverse=True)
            
            # 选择分数最高的节目
            selected_programs.append(scored_programs[0][1])
    
    # 第三步：处理时间重叠的节目
    final_programs = []
    # 按开始时间排序
    selected_programs.sort(key=lambda p: p.get('start', ''))
    
    for prog in selected_programs:
        # 检查是否与已选择的节目重叠
        overlap_found = False
        for existing_prog in final_programs:
            if programs_overlap(prog, existing_prog, 3):  # 3分钟重叠阈值
                # 有重叠，比较质量
                prog_score = get_program_quality(prog)
                existing_score = get_program_quality(existing_prog)
                
                if prog_score > existing_score:
                    # 用质量更高的替换
                    final_programs.remove(existing_prog)
                    final_programs.append(prog)
                # 如果质量相同或更低，跳过当前节目
                overlap_found = True
                break
        
        if not overlap_found:
            final_programs.append(prog)
    
    # 第四步：确保时间连续性
    final_programs.sort(key=lambda p: p.get('start', ''))
    for i in range(len(final_programs) - 1):
        current = final_programs[i]
        next_prog = final_programs[i + 1]
        
        current_stop_str = current.get('stop', '')
        next_start_str = next_prog.get('start', '')
        
        current_stop = parse_epg_time(current_stop_str)
        next_start = parse_epg_time(next_start_str)
        
        if current_stop and next_start and current_stop > next_start:
            # 调整当前节目的结束时间为下一个节目的开始时间
            adjusted_stop = next_start - datetime.timedelta(seconds=1)
            current.set('stop', adjusted_stop.strftime("%Y%m%d%H%M%S +0800"))
    
    return final_programs

def filter_programs_by_date(programs_by_channel):
    """过滤节目：保留当天、前一天以及当天之后的所有节目"""
    now = datetime.datetime.now(TIMEZONE)
    
    # 当天的开始时间（00:00:00）
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # 前一天的开始时间
    yesterday_start = today_start - datetime.timedelta(days=1)
    
    filtered_programs = {}
    total_before = 0
    total_after = 0
    
    for channel_id, programs in programs_by_channel.items():
        filtered = []
        for prog in programs:
            start_time_str = prog.get('start', '')
            start_time = parse_epg_time(start_time_str)
            
            if start_time:
                # 保留：前一天、当天、以及当天之后的所有节目
                if start_time >= yesterday_start:
                    filtered.append(prog)
        
        filtered_programs[channel_id] = filtered
        total_before += len(programs)
        total_after += len(filtered)
    
    logging.info(f"节目时间过滤: 从 {total_before} 个过滤到 {total_after} 个 (保留昨天、今天和未来)")
    return filtered_programs

def try_parse_xml(content, url):
    """尝试多种方式解析XML"""
    # 首先尝试直接解析
    try:
        root = ET.fromstring(content)
        return root
    except ET.ParseError:
        pass
    
    # 尝试不同编码
    encodings_to_try = ['utf-8', 'gb2312', 'gbk', 'latin-1', 'iso-8859-1']
    
    for encoding in encodings_to_try:
        try:
            root = ET.fromstring(content.decode(encoding, errors='ignore'))
            return root
        except (UnicodeDecodeError, ET.ParseError):
            continue
    
    return None

def process_sources(urls, alias_mapping, config_names):
    channels = {}  # 频道ID: 频道节点
    programmes = defaultdict(list)  # 频道ID: [节目列表]
    
    now = datetime.datetime.now(TIMEZONE)
    yesterday_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(days=1)
    logging.info(f"当前时间: {now}, 抓取时间范围: {yesterday_start} 之后")

    for url_index, url in enumerate(urls):
        try:
            # 获取并解析EPG数据
            logging.info(f"Processing [{url_index+1}/{len(urls)}]: {url}")
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            
            # 判断是否是gzip格式
            content = None
            try:
                # 先尝试作为gzip解压
                content = gzip.decompress(response.content)
            except Exception:
                # 如果不是gzip，直接使用原始内容
                content = response.content
            
            # 尝试解析XML
            root = try_parse_xml(content, url)
            if root is None:
                logging.warning(f"跳过无法解析的源: {url}")
                continue
            
            # 构建频道映射表 - 使用你原来的逻辑
            channel_map = {}
            for channel in root.findall('channel'):
                channel_id = channel.get('id')
                # 获取display-name元素
                display_name_elem = channel.find('display-name[@lang="zh"]')
                if display_name_elem is None:
                     display_name_elem = channel.find('display-name')
                if display_name_elem is None or display_name_elem.text is None:
                     continue
                display_name = display_name_elem.text.strip()
                
                # 使用你原来的map_channel函数
                mapped_id = map_channel(display_name, config_names, alias_mapping)
                if mapped_id:
                    channel_map[channel_id] = mapped_id
                    if mapped_id not in channels:
                        new_channel = deepcopy(channel)
                        new_channel.set('id', mapped_id)
                        # 清理旧display-name
                        for dn in new_channel.findall('display-name'):
                            new_channel.remove(dn)
                        ET.SubElement(new_channel, 'display-name', {'lang': 'zh'}).text = mapped_id
                        channels[mapped_id] = new_channel
            
            # 处理节目信息
            prog_count = 0
            for programme in root.findall('programme'):
                original_id = programme.get('channel')
                mapped_id = channel_map.get(original_id)
                if not mapped_id:
                    continue
                
                # 处理所有节目（稍后统一过滤）
                processed_prog = process_programme(programme, mapped_id)
                if processed_prog is not None:
                    programmes[mapped_id].append(processed_prog)
                    prog_count += 1
            
            logging.info(f"Processed: {len(channel_map)} channels, {prog_count} programmes from {url}")
            
        except requests.exceptions.Timeout:
            logging.warning(f"请求超时: {url}")
        except requests.exceptions.RequestException as e:
            logging.error(f"网络请求失败 {url}: {e}")
        except Exception as e:
            logging.error(f"处理失败 {url}: {e}")
    
    # 按日期过滤节目：保留昨天、今天和未来节目
    programmes = filter_programs_by_date(programmes)
    
    # 生成最终XML
    root = ET.Element('tv')
    
    # 添加频道 - 先添加找到的频道
    for channel in channels.values():
        root.append(deepcopy(channel))
    
    # 添加节目（应用完整的去重策略）
    total_progs = 0
    for channel_id, progs in programmes.items():
        if not progs:
            continue
            
        # 应用去重策略
        unique_progs = deduplicate_programs(progs)
        total_progs += len(unique_progs)
        root.extend(unique_progs)
        
        logging.info(f"频道 {channel_id}: 原始节目 {len(progs)} 个, 去重后 {len(unique_progs)} 个")
    
    # 保存压缩文件
    xml_str = ET.tostring(root, encoding='utf-8')
    try:
        with gzip.open(output_file_gz, 'wb') as f:
            f.write(b'<?xml version="1.0" encoding="utf-8"?>\n')
            f.write(b'<!DOCTYPE tv SYSTEM "xmltv.dtd">\n')
            f.write(xml_str)
        logging.info(f"EPG生成完成: {len(channels)} 个频道, {total_progs} 个节目")
        
        # 显示文件大小
        file_size = os.path.getsize(output_file_gz)
        logging.info(f"输出文件大小: {file_size / 1024 / 1024:.2f} MB")
        
    except Exception as e:
        logging.error(f"保存EPG文件失败: {e}")

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 初始化配置（从合并的文件中加载）
    config_names, alias_mapping = load_config_and_alias(config_file)
    
    # 数据源列表
    epg_urls = [
        'https://raw.githubusercontent.com/lxxcp/epg/main/tvmao.xml.gz',
        'https://raw.githubusercontent.com/plsy1/epg/main/e/seven-days.xml.gz',
        'https://raw.githubusercontent.com/Li-Xingyu/ZJ_IPTV_EPG/main/epg.xml',
        'https://raw.githubusercontent.com/zzq1234567890/epg/main/swepg.xml.gz',
        'https://gitee.com/taksssss/tv/raw/main/epg/51zmte1.xml.gz',
        'https://gitee.com/taksssss/tv/raw/main/epg/51zmte2.xml.gz',
        'https://epg.zsdc.eu.org/t.xml',
        'http://liliu.serv00.net/epg/all.xml.gz',
 	'https://epg.pw/xmltv/epg_CN.xml.gz',
        'https://epg.pw/xmltv/epg_TW.xml.gz',
        'https://epg.pw/xmltv/epg_HK.xml.gz',
        'https://gitee.com/taksssss/tv/raw/main/epg/erw.xml.gz',
        'https://gitee.com/taksssss/tv/raw/main/epg/112114.xml.gz',
        'https://gitee.com/taksssss/tv/raw/main/epg/epgpw_cn.xml.gz',
        'https://gitee.com/taksssss/tv/raw/main/epg/epgpw_hk.xml.gz',
        'https://gitee.com/taksssss/tv/raw/main/epg/epgpw_tw.xml.gz',
        'https://raw.githubusercontent.com/zsz520/epg/main/bjiptv.xml.gz',
        'https://raw.githubusercontent.com/zsz520/epg/main/chuanliu.xml.gz',
        'https://raw.githubusercontent.com/zsz520/epg/main/cqcu.xml.gz',
        'https://raw.githubusercontent.com/zsz520/epg/main/cqiptv.xml.gz',
        'https://raw.githubusercontent.com/zsz520/epg/main/cqlaidian.xml.gz',
        'https://raw.githubusercontent.com/zsz520/epg/main/fjyd.xml.gz',
        'https://raw.githubusercontent.com/zsz520/epg/main/migu.xml.gz',
        'http://139.199.229.98:8989/EPG',
        'https://raw.githubusercontent.com/zzq12345/epgtest/main/epganywhere.xml',
 	'https://raw.githubusercontent.com/zzq12345/epgtest/main/epgbaidu.xml',
        'https://raw.githubusercontent.com/zzq12345/epgtest/main/epghebeiiptv1.xml',
        'https://raw.githubusercontent.com/zzq12345/epgtest/main/epgnewhebei.xml',
        'https://raw.githubusercontent.com/zzq12345/epgtest/main/epgguangdong.xml.gz',
        'https://raw.githubusercontent.com/zzq12345/epgtest/main/epgnewguangdong.xml',
        'https://raw.githubusercontent.com/zzq12345/epgtest/main/epgnewshanghai.xml',
        'https://raw.githubusercontent.com/zzq12345/epgtest/main/epgyidong.xml',
        'https://raw.githubusercontent.com/zzq12345/epgtest/main/epgmytvsuper.xml',
        'https://raw.githubusercontent.com/zzq12345/epgtest/main/epgtvsou.xml',
        'https://epg.136605.xyz/9days.xml',
        'https://raw.githubusercontent.com/peterHchina/iptv/main/EPG.xml',

    ]
    
    process_sources(epg_urls, alias_mapping, config_names)