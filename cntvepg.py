# -*- coding: utf-8 -*-
import pytz
import sys
import requests
import gzip
from datetime import datetime, timedelta
from html import escape

tz = pytz.timezone('Asia/Shanghai')

# 频道列表（你原来的列表）
cctv_channel = ['cctv1', 'cctv2', 'cctv3', 'cctv4', 'cctv5', 'cctv5plus', 'cctv6','cctv7', 'cctv8', 'cctvjilu', 'cctv10', 'cctv11', 'cctv12','cctv13','cctvchild','cctv15', 'cctv16', 'cctv17', 'cctveurope', 'cctvamerica', 'cctvxiyu', 'cctv4k', 'cctvarabic', 'cctvfrench', 'cctvrussian','shijiedili', 'dianshigouwu', 'taiqiu', 'jingpin', 'shishang', 'hjjc','zhinan', 'diyijuchang', 'fyjc', 'cctvfyzq', 'fyyy','cctvzhengquanzixun','cctvgaowang', 'faxianzhilv','cetv1', 'cetv2', 'xianggangweishi', 'cetv3', 'cetv4', 'cctvdoc', 'cctv9', 'btv1', 'btvjishi', 'dongfang', 'hunan', 'shandong', 'zhejiang', 'jiangsu','guangdong', 'dongnan', 'anhui',  'zhongxueshengpindao', 'faxianzhilv',  'wsjk', 'gansu', 'liaoning', 'cctvlaogushi', 'neimenggu', 'ningxia', 'qinghai', 'xiamen', 'yunnan','chongqing', 'jiangxi', 'shan1xi', 'shan3xi', 'shenzhen', 'sichuan', 'tianjin', 'guangxi', 'guizhou', 'hebei', 'henan', 'heilongjiang', 'hubei', 'jilin','yanbian', 'xizang', 'xinjiang','bingtuan', 'btvchild', 'sdetv', 'shuhua', 'xianfengjilu', 'shuowenjiezi', 'kuailechuidiao', 'zaoqijiaoyu', 'nbtv1', 'nbtv2', 'nbtv3', 'nbtv4', 'wenwubaoku','cctvliyuan', 'wushushijie', 'cctvqimo', 'huanqiuqiguan', 'btv2', 'btv3', 'btv4', 'btv5', 'btv7', 'btv9', 'btvinternational']

def get_epg_data(session, cid, epgdate):
    """获取EPG数据"""
    try:
        api = f"http://api.cntv.cn/epg/epginfo?c={cid}&d={epgdate}"
        response = session.get(api, timeout=10)
        response.raise_for_status()
        print(f"✅ 成功抓取频道 {cid} 数据")
        return response.json()
    except Exception as e:
        print(f"❌ 获取 {cid} 数据失败: {str(e)}", file=sys.stderr)
        return None

def getChannelCNTV(fhandle, channelIDs):
    """获取频道基本信息"""
    session = requests.Session()
    epgdate = datetime.now(tz).strftime('%Y%m%d')

    print("\n📺 开始获取频道信息...")
    for i, channel in enumerate(channelIDs, 1):
        print(f"  处理频道 {i}/{len(channelIDs)}: {channel}", end="\r")

        epgdata = get_epg_data(session, channel, epgdate)
        if epgdata is None or channel not in epgdata:
            continue

        # 写入频道信息
        channel_name = epgdata[channel].get("channelName", channel.upper())
        fhandle.write(f'  <channel id="{channel.upper()}">\n')
        fhandle.write(f'    <display-name>{channel_name}</display-name>\n')
        fhandle.write('  </channel>\n')

    print(f"\n✅ 频道信息获取完成，共处理 {len(channelIDs)} 个频道")

def getChannelEPG(fhandle, channelIDs):
    """获取节目单信息"""
    session = requests.Session()
    today = datetime.now(tz)
    dates = [today + timedelta(days=i) for i in range(5)]  # 获取5天数据

    print("\n📅 开始获取节目单...")
    total_channels = len(channelIDs)

    for channel_idx, channel in enumerate(channelIDs, 1):
        print(f"  处理频道 {channel_idx}/{total_channels}: {channel}")

        for date_idx, date in enumerate(dates, 1):
            epgdate = date.strftime('%Y%m%d')
            print(f"    日期 {date_idx}/{len(dates)}: {epgdate}", end="\r")

            epgdata = get_epg_data(session, channel, epgdate)
            if epgdata is None or channel not in epgdata:
                continue

            programs = epgdata[channel].get('program', [])

            if programs:
                print(f"    日期 {date_idx}/{len(dates)}: {epgdate} - 找到 {len(programs)} 个节目")

            for detail in programs:
                try:
                    # 处理时间戳（API返回的是秒级时间戳）
                    st = detail['st']
                    et = detail['et']

                    # 转换为XMLTV格式的时间
                    start_dt = datetime.fromtimestamp(st, tz)
                    end_dt = datetime.fromtimestamp(et, tz)

                    # 格式化为：YYYYMMDDHHMMSS +0800
                    start_str = start_dt.strftime('%Y%m%d%H%M%S %z')
                    stop_str = end_dt.strftime('%Y%m%d%H%M%S %z')

                    # 处理跨天节目（如果开始时间>结束时间）
                    if start_str[:8] != stop_str[:8]:  # 日期不同
                        # 可能是跨天节目，确保时间格式正确
                        print(f"⚠️  跨天节目: {detail['t']} ({start_str[:8]} → {stop_str[:8]})")

                    # 写入节目信息（严格按照示例格式）
                    fhandle.write(f'  <programme start="{start_str}" stop="{stop_str}" channel="{channel.upper()}">\n')
                    fhandle.write(f'    <title>{escape(detail["t"])}</title>\n')
                    fhandle.write('  </programme>\n')

                except Exception as e:
                    print(f"    ⚠️ 处理节目失败: {detail.get('t', '未知节目')} - {e}")
                    continue

    print("\n✅ 节目单获取完成")

def main():
    """主函数"""
    print("=" * 60)
    print("🎬 CNTV EPG 抓取工具 - 精简版")
    print("=" * 60)

    try:
        with gzip.open('cntvepg.xml.gz', 'wt', encoding='utf-8') as f:
            # 写入XML头部
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('<tv>\n')

            # 获取频道信息
            getChannelCNTV(f, cctv_channel)

            # 获取节目信息
            getChannelEPG(f, cctv_channel)

            # 写入XML尾部
            f.write('</tv>\n')

        print("\n" + "=" * 60)
        print("🎉 EPG文件生成成功！")
        print("📁 文件位置: cntvepg.xml.gz")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ 生成EPG文件失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()