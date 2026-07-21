#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# Reconfigure standard I/O encoding to UTF-8 to avoid encoding errors on Windows (CP950)
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# MLB Team Metadata: Chinese Name, Abbreviation, and Official Color Hex
TEAM_META = {
    108: {"name": "洛杉磯天使", "abbr": "LAA", "color": "#BA0021"},
    109: {"name": "亞利桑那響尾蛇", "abbr": "ARI", "color": "#A71930"},
    110: {"name": "巴爾的摩金鶯", "abbr": "BAL", "color": "#DF4601"},
    111: {"name": "波士頓紅襪", "abbr": "BOS", "color": "#BD3039"},
    112: {"name": "芝加哥小熊", "abbr": "CHC", "color": "#0E3386"},
    113: {"name": "辛辛那提紅人", "abbr": "CIN", "color": "#C6011F"},
    114: {"name": "克里夫蘭守護者", "abbr": "CLE", "color": "#0C2340"},
    115: {"name": "科羅拉多洛磯", "abbr": "COL", "color": "#333366"},
    116: {"name": "底特律老虎", "abbr": "DET", "color": "#0C2340"},
    117: {"name": "休士頓太空人", "abbr": "HOU", "color": "#EB6E1F"},
    118: {"name": "堪薩斯皇家", "abbr": "KCR", "color": "#004687"},
    119: {"name": "洛杉磯道奇", "abbr": "LAD", "color": "#005A9C"},
    120: {"name": "華盛頓國民", "abbr": "WSH", "color": "#AB0003"},
    121: {"name": "紐約大都會", "abbr": "NYM", "color": "#FF5910"},
    133: {"name": "奧克蘭運動家", "abbr": "OAK", "color": "#003831"},
    134: {"name": "匹茲堡海盜", "abbr": "PIT", "color": "#FDB827"},
    135: {"name": "聖地牙哥教士", "abbr": "SD", "color": "#2F241D"},
    136: {"name": "西雅圖水手", "abbr": "SEA", "color": "#0C2C56"},
    137: {"name": "舊金山巨人", "abbr": "SF", "color": "#FD5A1E"},
    138: {"name": "聖路易紅雀", "abbr": "STL", "color": "#C41E3A"},
    139: {"name": "坦帕灣光芒", "abbr": "TB", "color": "#092C5C"},
    140: {"name": "德州遊騎兵", "abbr": "TEX", "color": "#003278"},
    141: {"name": "多倫多藍鳥", "abbr": "TOR", "color": "#134A8E"},
    142: {"name": "明尼蘇達雙城", "abbr": "MIN", "color": "#002B5C"},
    143: {"name": "費城費城人", "abbr": "PHI", "color": "#E81828"},
    144: {"name": "亞特蘭大勇士", "abbr": "ATL", "color": "#CE1141"},
    145: {"name": "芝加哥白襪", "abbr": "CWS", "color": "#27251F"},
    146: {"name": "邁阿密馬林魚", "abbr": "MIA", "color": "#00A3E0"},
    147: {"name": "紐約洋基", "abbr": "NYY", "color": "#0C2340"},
    158: {"name": "密爾瓦基釀酒人", "abbr": "MIL", "color": "#122853"}
}

POSTPONED_REASONS = {
    "Rain": "因雨延期",
    "Wet Grounds": "場地濕滑延期",
    "Cold": "低溫延期",
    "Inclement Weather": "惡劣天氣延期",
    "Snow": "因雪延期",
    "Postponed": "賽事延期"
}

def get_taipei_now():
    tz_taipei = timezone(timedelta(hours=8))
    return datetime.now(timezone.utc).astimezone(tz_taipei)

def get_yesterday_str():
    now = get_taipei_now()
    yesterday = now - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")

def fetch_json(url, retries=3, delay=5):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            print(f"Error fetching {url} (attempt {attempt + 1}/{retries}): {e}", file=sys.stderr)
            if attempt < retries - 1:
                time.sleep(delay)
    return None

def format_cumulative(count, season_total):
    if count == 1:
        return f"1({season_total})"
    else:
        parts = []
        for i in range(count):
            parts.append(f"({season_total - count + 1 + i})")
        return f"{count}{''.join(parts)}"

def get_team_info(team_id, english_name):
    if team_id in TEAM_META:
        return TEAM_META[team_id]
    return {"name": english_name, "abbr": english_name[:3].upper(), "color": "#6b7280"}

def check_all_games_finalized(date_str):
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}&hydrate=linescore,decisions"
    data = fetch_json(url)
    if not data or not data.get("dates"):
        return True, []
    
    games = data["dates"][0].get("games", [])
    if not games:
        return True, []
        
    for game in games:
        state = game.get("status", {}).get("abstractGameState")
        detailed = game.get("status", {}).get("detailedState")
        # Games are NOT finalized if they are Preview or Live (excluding explicitly Postponed/Cancelled)
        if state in ["Preview", "Live"] and detailed not in ["Postponed", "Cancelled"]:
            return False, games
            
    return True, games

def fetch_game_news(game_pk, away_abbr, home_abbr, away_r, home_r):
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/content"
    backup_headline = f"{away_abbr} @ {home_abbr} 賽事總結"
    backup_blurb = f"終場比分 {away_abbr} {away_r} - {home_abbr} {home_r}。本場比賽目前無官方文字摘要。"
    
    data = fetch_json(url)
    if not data:
        return {"headline": backup_headline, "blurb": backup_blurb}
        
    try:
        recap = data.get("editorial", {}).get("recap", {}).get("mlb", {})
        headline = recap.get("headline")
        blurb = recap.get("blurb")
        if headline and blurb:
            return {"headline": headline, "blurb": blurb}
    except Exception as e:
        print(f"Warning: Error parsing news for game {game_pk} ({e})", file=sys.stderr)
        
    return {"headline": backup_headline, "blurb": backup_blurb}

def fetch_daily_transactions(date_str):
    url = f"https://statsapi.mlb.com/api/v1/transactions?date={date_str}&sportId=1"
    data = fetch_json(url)
    if not data or not data.get("transactions"):
        return []
        
    tx_list = []
    seen_descriptions = set()
    
    for tx in data["transactions"]:
        desc = tx.get("description")
        if not desc or desc in seen_descriptions:
            continue
        desc_lower = desc.lower()
        if "minor league" in desc_lower:
            continue
        if "signed" in desc_lower:
            indicators = ["free agent", "major league", "extension", "arbitration", "-year"]
            if not any(ind in desc_lower for ind in indicators):
                continue
        seen_descriptions.add(desc)
        
        to_team_id = tx.get("toTeam", {}).get("id")
        from_team_id = tx.get("fromTeam", {}).get("id")
        
        person_info = tx.get("person", {})
        person_id = person_info.get("id")
        person_name = person_info.get("fullName")
        
        if person_id and person_name and person_name in desc:
            link_html = f'<a href="https://baseballsavant.mlb.com/savant-player/{person_id}" target="_blank" class="player-link">{person_name}</a>'
            desc = desc.replace(person_name, link_html)
            
        tx_list.append({
            "to_team_id": to_team_id,
            "from_team_id": from_team_id,
            "description": desc
        })
        
    return tx_list

def build_transactions_section_html(daily_tx_list):
    grouped_other_tx = {}
    for tx in daily_tx_list:
        to_team_id = tx["to_team_id"]
        from_team_id = tx["from_team_id"]
        
        team_id = None
        if to_team_id in TEAM_META:
            team_id = to_team_id
        elif from_team_id in TEAM_META:
            team_id = from_team_id
            
        if team_id:
            grouped_other_tx.setdefault(team_id, []).append(tx["description"])
        else:
            grouped_other_tx.setdefault("Other", []).append(tx["description"])
            
    total_other_tx = sum(len(descs) for descs in grouped_other_tx.values())
    
    if total_other_tx > 0:
        other_cards_html = []
        sorted_other_team_ids = sorted([tid for tid in grouped_other_tx.keys() if tid != "Other"])
        if "Other" in grouped_other_tx:
            sorted_other_team_ids.append("Other")
            
        for tid in sorted_other_team_ids:
            descs = grouped_other_tx[tid]
            li_parts = [f"<li>{desc}</li>" for desc in descs]
            
            if tid == "Other":
                logo_url = "https://www.mlbstatic.com/team-logos/team-cap-on-dark/mlb.svg"
                team_name = "其他異動"
            else:
                logo_url = f"https://www.mlbstatic.com/team-logos/team-cap-on-dark/{tid}.svg"
                team_name = TEAM_META[tid]["name"]
                
            team_card_html = f"""        <!-- Team Transactions: {tid} -->
        <div class="team-tx-card">
          <div class="team-tx-header">
            <img src="{logo_url}" class="team-tx-logo" alt="logo">
            <span class="team-tx-name">{team_name}</span>
          </div>
          <ul class="team-tx-list">
            {"".join(li_parts)}
          </ul>
        </div>"""
            other_cards_html.append(team_card_html)
            
        return f"""    <section class="transactions-section">
      <h2 class="transactions-header">
        <span>其他球隊人事異動</span>
        <span class="tx-badge">{total_other_tx}</span>
      </h2>
      <div class="transactions-grid">
{"\n".join(other_cards_html)}
      </div>
    </section>"""
    else:
        return f"""    <section class="transactions-section">
      <h2 class="transactions-header">
        <span>其他球隊人事異動</span>
        <span class="tx-badge">0</span>
      </h2>
      <div style="color:var(--text-muted);font-size:0.85rem;padding:1.5rem;text-align:center;background:rgba(255,255,255,0.01);border:1px dashed var(--card-border);border-radius:12px;">
        本日無其他球隊人事異動。
      </div>
    </section>"""

def generate_report(date_str, games):
    print(f"Generating report for {date_str} with {len(games)} games...")
    total_games = len(games)
    postponed_count = 0
    games_cards_html = []
    
    # Simple summary log for index metadata
    key_matchups_summary = []
    
    # Fetch daily transactions
    daily_tx_list = fetch_daily_transactions(date_str)
    
    for game in games:
        game_pk = game["gamePk"]
        detailed_state = game.get("status", {}).get("detailedState")
        
        # Get team basics
        away_team_obj = game["teams"]["away"]["team"]
        home_team_obj = game["teams"]["home"]["team"]
        
        away_info = get_team_info(away_team_obj["id"], away_team_obj["name"])
        home_info = get_team_info(home_team_obj["id"], home_team_obj["name"])
        
        # 1. Postponed Game Card
        if detailed_state in ["Postponed", "Cancelled"]:
            postponed_count += 1
            reason_eng = game.get("status", {}).get("reason", "Postponed")
            reason_chi = POSTPONED_REASONS.get(reason_eng, "賽事延期")
            
            card_html = f"""
      <!-- Game Card: {away_info['abbr']} @ {home_info['abbr']} (Postponed) -->
      <div class="game-card" style="grid-template-columns: 1fr;">
        <div class="postponed-game-body">
          <div class="postponed-icon">🌧️</div>
          <div class="postponed-title">{away_info['name']} @ {home_info['name']}</div>
          <p>原定於今日舉行的例行賽事，{reason_chi} (Postponed - {reason_eng})。補賽日期將另行公布。</p>
          <span class="status-badge postponed">Postponed</span>
        </div>
      </div>
"""
            games_cards_html.append(card_html)
            continue
            
        # 2. Played Game Card (Final or Final/Innings)
        # Fetch boxscore data
        boxscore_url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
        boxscore = fetch_json(boxscore_url)
        if not boxscore:
            print(f"Warning: Could not fetch boxscore for game {game_pk}. Skipping details.", file=sys.stderr)
            continue
            
        # Extract R-H-E from linescore
        linescore = game.get("linescore", {})
        away_r = linescore.get("teams", {}).get("away", {}).get("runs", 0)
        away_h = linescore.get("teams", {}).get("away", {}).get("hits", 0)
        away_e = linescore.get("teams", {}).get("away", {}).get("errors", 0)
        
        home_r = linescore.get("teams", {}).get("home", {}).get("runs", 0)
        home_h = linescore.get("teams", {}).get("home", {}).get("hits", 0)
        home_e = linescore.get("teams", {}).get("home", {}).get("errors", 0)
        
        # Determine status badge text (Final or Final/X)
        innings_played = len(linescore.get("innings", []))
        if innings_played == 9 or innings_played == 0:
            status_text = "Final"
            status_class = "final"
        else:
            status_text = f"Final/{innings_played}"
            status_class = "extra"
            
        # Determine Winner
        away_winner = away_r > home_r
        away_winner_class = "winner-row" if away_winner else ""
        home_winner_class = "winner-row" if not away_winner else ""
        
        # Get team records
        away_rec = game["teams"]["away"].get("leagueRecord", {})
        home_rec = game["teams"]["home"].get("leagueRecord", {})
        away_rec_str = f"({away_rec.get('wins', 0)}-{away_rec.get('losses', 0)})"
        home_rec_str = f"({home_rec.get('wins', 0)}-{home_rec.get('losses', 0)})"
        
        # Extract Decisions info
        dec_obj = game.get("decisions", {})
        w_pitcher_str = "無"
        l_pitcher_str = "無"
        s_pitcher_str = "無"
        
        # Resolve W-L-S Pitchers W-L stats from boxscore seasonStats
        players_away = boxscore["teams"]["away"].get("players", {})
        players_home = boxscore["teams"]["home"].get("players", {})
        
        def find_pitcher_stats(pitcher_id):
            p_key = f"ID{pitcher_id}"
            p_obj = players_away.get(p_key) or players_home.get(p_key)
            if p_obj:
                name = p_obj["person"]["fullName"]
                p_stats = p_obj.get("seasonStats", {}).get("pitching", {})
                return name, p_stats
            return None, None
            
        if "winner" in dec_obj:
            p_id = dec_obj["winner"]["id"]
            name, stats = find_pitcher_stats(p_id)
            if name:
                w_pitcher_str = f'<a href="https://baseballsavant.mlb.com/savant-player/{p_id}" target="_blank" class="player-link">{name}</a> ({stats.get("wins", 0)}-{stats.get("losses", 0)})'
            else:
                w_pitcher_str = f'<a href="https://baseballsavant.mlb.com/savant-player/{p_id}" target="_blank" class="player-link">{dec_obj["winner"]["fullName"]}</a>'
                
        if "loser" in dec_obj:
            p_id = dec_obj["loser"]["id"]
            name, stats = find_pitcher_stats(p_id)
            if name:
                l_pitcher_str = f'<a href="https://baseballsavant.mlb.com/savant-player/{p_id}" target="_blank" class="player-link">{name}</a> ({stats.get("wins", 0)}-{stats.get("losses", 0)})'
            else:
                l_pitcher_str = f'<a href="https://baseballsavant.mlb.com/savant-player/{p_id}" target="_blank" class="player-link">{dec_obj["loser"]["fullName"]}</a>'
                
        if "save" in dec_obj:
            p_id = dec_obj["save"]["id"]
            name, stats = find_pitcher_stats(p_id)
            if name:
                s_pitcher_str = f'<a href="https://baseballsavant.mlb.com/savant-player/{p_id}" target="_blank" class="player-link">{name}</a> ({stats.get("saves", 0)})'
            else:
                s_pitcher_str = f'<a href="https://baseballsavant.mlb.com/savant-player/{p_id}" target="_blank" class="player-link">{dec_obj["save"]["fullName"]}</a>'
                
        # Determine starting pitchers for away and home teams
        away_pitchers = boxscore["teams"]["away"].get("pitchers", [])
        home_pitchers = boxscore["teams"]["home"].get("pitchers", [])
        away_starter_id = away_pitchers[0] if away_pitchers else None
        home_starter_id = home_pitchers[0] if home_pitchers else None

        # Parse Player Highlights (Notable Performers) using unified score formulas
        all_candidates = []
        
        # Loop teams
        for team_type, team_abbr in [("away", away_info["abbr"]), ("home", home_info["abbr"])]:
            team_players = boxscore["teams"][team_type].get("players", {})
            for p_id, p_info in team_players.items():
                p_name = p_info["person"]["fullName"]
                p_id_int = p_info["person"]["id"]
                pos = p_info.get("position", {}).get("abbreviation", "")
                
                # Batter check
                bat_stats = p_info.get("stats", {}).get("batting", {})
                if bat_stats and bat_stats.get("atBats", 0) > 0:
                    ab = bat_stats.get("atBats", 0)
                    r = bat_stats.get("runs", 0)
                    h = bat_stats.get("hits", 0)
                    d = bat_stats.get("doubles", 0)
                    t = bat_stats.get("triples", 0)
                    hr = bat_stats.get("homeRuns", 0)
                    rbi = bat_stats.get("rbi", 0)
                    bb = bat_stats.get("baseOnBalls", 0)
                    so = bat_stats.get("strikeOuts", 0)
                    sb = bat_stats.get("stolenBases", 0)
                    
                    # Score = H*1 + 2B*2 + 3B*3 + HR*4 + RBI*1.5 + R*1 + SB*1 + BB*1
                    score = h * 1.0 + d * 2.0 + t * 3.0 + hr * 4.0 + rbi * 1.5 + r * 1.0 + sb * 1.0 + bb * 1.0
                    
                    season_bat = p_info.get("seasonStats", {}).get("batting", {})
                    season_hr = season_bat.get("homeRuns", 0)
                    season_sb = season_bat.get("stolenBases", 0)
                    
                    all_candidates.append({
                        "type": "batter",
                        "id": p_id_int,
                        "name": p_name,
                        "team": team_abbr,
                        "pos": pos,
                        "score": score,
                        "stats": {
                            "ab": ab, "r": r, "h": h, "rbi": rbi, "bb": bb, "so": so,
                            "hr": hr, "sb": sb, "season_hr": season_hr, "season_sb": season_sb
                        }
                    })
                        
                # Pitcher check
                pit_stats = p_info.get("stats", {}).get("pitching", {})
                if pit_stats and pit_stats.get("inningsPitched") and pit_stats.get("inningsPitched") != "0.0":
                    ip_str = pit_stats.get("inningsPitched")
                    h = pit_stats.get("hits", 0)
                    r = pit_stats.get("runs", 0)
                    er = pit_stats.get("earnedRuns", 0)
                    bb = pit_stats.get("baseOnBalls", 0)
                    so = pit_stats.get("strikeOuts", 0)
                    num_pitches = pit_stats.get("numberOfPitches", 0)
                    strikes = pit_stats.get("strikes", 0)
                    
                    is_starter = (p_id_int == away_starter_id) or (p_id_int == home_starter_id)
                    
                    # Resolve IP outs
                    parts = ip_str.split('.')
                    innings = int(parts[0])
                    outs = int(parts[1]) if len(parts) > 1 else 0
                    ip_outs = innings * 3 + outs
                    
                    # Decisions info
                    wins_game = pit_stats.get("wins", 0)
                    saves_game = pit_stats.get("saves", 0)
                    losses_game = pit_stats.get("losses", 0)
                    
                    is_win = wins_game > 0
                    is_save = saves_game > 0
                    
                    # Score = IP_outs + K*1 - ER*2 - H*1 - BB*1 + (Win ? 5 : 0) + (Save ? 4 : 0)
                    score = ip_outs + so * 1.0 - er * 2.0 - h * 1.0 - bb * 1.0 + (5.0 if is_win else 0.0) + (4.0 if is_save else 0.0)
                    
                    season_pit = p_info.get("seasonStats", {}).get("pitching", {})
                    s_wins = season_pit.get("wins", 0)
                    s_losses = season_pit.get("losses", 0)
                    s_saves = season_pit.get("saves", 0)
                    
                    tag = ""
                    if is_win:
                        tag = f'<span class="performer-tag pitcher-win">W ({s_wins}-{s_losses})</span>'
                    elif is_save:
                        tag = f'<span class="performer-tag pitcher-win">S ({s_saves})</span>'
                    elif losses_game > 0:
                        tag = f'<span class="performer-tag" style="background: rgba(239, 68, 68, 0.1); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.2);">L ({s_wins}-{s_losses})</span>'
                    elif is_starter:
                        tag = '<span class="performer-tag" style="background: rgba(255, 255, 255, 0.05); color: var(--text-secondary); border: 1px solid var(--card-border);">Starter</span>'
                    else:
                        tag = '<span class="performer-tag" style="background: rgba(255, 255, 255, 0.05); color: var(--text-secondary); border: 1px solid var(--card-border);">Relief</span>'
                        
                    all_candidates.append({
                        "type": "pitcher",
                        "id": p_id_int,
                        "name": p_name,
                        "team": team_abbr,
                        "pos": pos,
                        "score": score,
                        "is_starter": is_starter,
                        "tag": tag,
                        "stats": {
                            "ip": ip_str, "h": h, "r": r, "er": er, "bb": bb, "so": so,
                            "pitches": num_pitches, "strikes": strikes
                        }
                    })
                        
        # Sort candidates by score descending
        all_candidates.sort(key=lambda x: x["score"], reverse=True)
        
        # Output candidate scores for debugging/logging
        print(f"  Game {game_pk} ({away_info['abbr']} @ {home_info['abbr']}) candidates score log:")
        for cand in all_candidates[:5]:
            print(f"    - {cand['name']} ({cand['team']} - {cand['type']}): score={cand['score']:.1f}")
        
        # Pick top performers using dual-threshold approach
        selected_candidates = []
        for idx, cand in enumerate(all_candidates):
            if idx < 3:
                # Guarantee top 3
                selected_candidates.append(cand)
            else:
                # Check thresholds
                if cand["type"] == "batter":
                    if cand["score"] >= 7.0:
                        selected_candidates.append(cand)
                elif cand["type"] == "pitcher":
                    threshold = 15.0 if cand["is_starter"] else 8.0
                    if cand["score"] >= threshold:
                        selected_candidates.append(cand)
                        
        # Split back to batters and pitchers
        selected_batters = [c for c in selected_candidates if c["type"] == "batter"]
        selected_pitchers = [c for c in selected_candidates if c["type"] == "pitcher"]
        
        # Build batters HTML list
        batters_html_list = []
        for b in selected_batters:
            b_stats = b["stats"]
            tags = []
            if b_stats["hr"] > 0:
                tags.append(f'<span class="performer-tag hr">HR: {format_cumulative(b_stats["hr"], b_stats["season_hr"])}</span>')
            if b_stats["sb"] > 0:
                tags.append(f'<span class="performer-tag sb">SB: {format_cumulative(b_stats["sb"], b_stats["season_sb"])}</span>')
                
            tags_str = " ".join(tags)
            
            batter_card = f"""              <!-- Batter Highlight: {b['name']} -->
              <div class="performer-item">
                <div class="performer-name-row">
                  <div>
                    <span class="performer-name"><a href="https://baseballsavant.mlb.com/savant-player/{b['id']}" target="_blank" class="player-link">{b['name']}</a></span>
                    <span class="performer-team">{b['team']} · {b['pos']}</span>
                  </div>
                  {tags_str}
                </div>
                <div class="performer-stats">
                  單場表現：打數 <span class="performer-stats-numbers">{b_stats['ab']}</span> | 得分 <span class="performer-stats-numbers">{b_stats['r']}</span> | 安打 <span class="performer-stats-numbers">{b_stats['h']}</span> | 打點 <span class="performer-stats-numbers">{b_stats['rbi']}</span> | 保送 <span class="performer-stats-numbers">{b_stats['bb']}</span>
                </div>
              </div>"""
            batters_html_list.append(batter_card)
            
        # Build pitchers HTML list
        pitchers_html_list = []
        for p in selected_pitchers:
            p_stats = p["stats"]
            pitcher_card = f"""              <!-- Pitcher Highlight: {p['name']} -->
              <div class="performer-item">
                <div class="performer-name-row">
                  <div>
                    <span class="performer-name"><a href="https://baseballsavant.mlb.com/savant-player/{p['id']}" target="_blank" class="player-link">{p['name']}</a></span>
                    <span class="performer-team">{p['team']} · {p['pos']}</span>
                  </div>
                  {p['tag']}
                </div>
                <div class="performer-stats">
                  投球內容：局數 <span class="performer-stats-numbers">{p_stats['ip']}</span> | 被安打 <span class="performer-stats-numbers">{p_stats['h']}</span> | 失分 <span class="performer-stats-numbers">{p_stats['r']}</span> | 自責 <span class="performer-stats-numbers">{p_stats['er']}</span> | 保送 <span class="performer-stats-numbers">{p_stats['bb']}</span> | 三振 <span class="performer-stats-numbers">{p_stats['so']}</span> (球數 <span class="performer-stats-numbers">{p_stats['pitches']}/{p_stats['strikes']}</span>)
                </div>
              </div>"""
            pitchers_html_list.append(pitcher_card)
            
        batters_section_html = "\n".join(batters_html_list) if batters_html_list else "              <div style='color:var(--text-muted);font-size:0.85rem;padding:0.5rem;'>今日無突出打擊表現。</div>"
        pitchers_section_html = "\n".join(pitchers_html_list) if pitchers_html_list else "              <div style='color:var(--text-muted);font-size:0.85rem;padding:0.5rem;'>今日無突出投手表現。</div>"
        
        # Log summary sentence for index.html description
        summary_sentence = f"{away_info['abbr']} @ {home_info['abbr']}，比分 {away_r}:{home_r}"
        if selected_candidates:
            top_perf = selected_candidates[0]
            if top_perf["type"] == "batter" and top_perf["stats"]["hr"] > 0:
                summary_sentence += f"，{top_perf['name']} 擊出全壘打"
            elif top_perf["type"] == "pitcher" and top_perf["stats"]["so"] >= 5:
                summary_sentence += f"，{top_perf['name']} 投出 {top_perf['stats']['so']} 次三振"
        key_matchups_summary.append(summary_sentence)
        
        # Fetch game news (Headline & Blurb)
        news = fetch_game_news(game_pk, away_info["abbr"], home_info["abbr"], away_r, home_r)
        news_box_html = f"""            <div class="game-news-box">
              <h4 class="game-news-title">{news['headline']}</h4>
              <p class="game-news-content">{news['blurb']}</p>
            </div>"""

        # Roster Transactions for this game
        game_tx_html_parts = []
        
        def render_card_team_tx(team_id, team_info, tx_descriptions):
            tx_li_parts = [f"<li>{desc}</li>" for desc in tx_descriptions]
            logo_url = f"https://www.mlbstatic.com/team-logos/team-cap-on-dark/{team_id}.svg"
            return f"""              <div class="card-team-tx">
                <div class="card-team-tx-header">
                  <img src="{logo_url}" class="card-tx-logo" alt="{team_info['abbr']}">
                  <span class="card-tx-team-name">{team_info['name']}</span>
                </div>
                <ul class="card-tx-list">
                  {"".join(tx_li_parts)}
                </ul>
              </div>"""

        # Roster transactions matching away team
        away_tx_descs = [tx["description"] for tx in daily_tx_list if tx["to_team_id"] == away_team_obj["id"] or tx["from_team_id"] == away_team_obj["id"]]
        if away_tx_descs:
            game_tx_html_parts.append(render_card_team_tx(away_team_obj["id"], away_info, away_tx_descs))
            
        # Roster transactions matching home team
        home_tx_descs = [tx["description"] for tx in daily_tx_list if tx["to_team_id"] == home_team_obj["id"] or tx["from_team_id"] == home_team_obj["id"]]
        if home_tx_descs:
            game_tx_html_parts.append(render_card_team_tx(home_team_obj["id"], home_info, home_tx_descs))
            
        # Remove these matched transactions from the global list
        matched_descriptions = set(away_tx_descs + home_tx_descs)
        daily_tx_list = [tx for tx in daily_tx_list if tx["description"] not in matched_descriptions]
        
        game_tx_box_html = ""
        if game_tx_html_parts:
            game_tx_box_html = f"""            <div class="game-transactions-box">
              <h4 class="game-tx-sub-header">球員異動</h4>
              {"".join(game_tx_html_parts)}
            </div>"""
            
        # Build Game Card HTML
        card_html = f"""
      <!-- Game Card: {away_info['abbr']} @ {home_info['abbr']} -->
      <div class="game-card">
        <!-- Upper Part: Matchup & Scores (Full-width) -->
        <div class="game-card-upper">
          <div class="game-header">
            <span class="game-title">例行賽 #{game_pk}</span>
            <span class="status-badge {status_class}">{status_text}</span>
          </div>
          
          <div class="score-table-wrapper">
            <table class="score-table">
              <thead>
                <tr>
                  <th style="text-align: left; width: 50%;">隊伍</th>
                  <th>R</th>
                  <th>H</th>
                  <th>E</th>
                </tr>
              </thead>
              <tbody>
                <tr class="{away_winner_class}">
                  <td class="team-cell">
                    <img src="https://www.mlbstatic.com/team-logos/team-cap-on-dark/{away_team_obj['id']}.svg" class="team-logo" alt="{away_info['abbr']}">
                    <span>{away_info['name']} {away_rec_str}</span>
                  </td>
                  <td class="num-cell runs-cell">{away_r}</td>
                  <td class="num-cell">{away_h}</td>
                  <td class="num-cell">{away_e}</td>
                </tr>
                <tr class="{home_winner_class}">
                  <td class="team-cell">
                    <img src="https://www.mlbstatic.com/team-logos/team-cap-on-dark/{home_team_obj['id']}.svg" class="team-logo" alt="{home_info['abbr']}">
                    <span>{home_info['name']} {home_rec_str}</span>
                  </td>
                  <td class="num-cell runs-cell">{home_r}</td>
                  <td class="num-cell">{home_h}</td>
                  <td class="num-cell">{home_e}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Lower Part: Two Columns (Decisions & Highlights) -->
        <div class="game-card-lower">
          <!-- Left Column: Pitching Decisions & News Summary -->
          <div class="decisions-section">
            <h3 class="section-sub-header">投球決定</h3>
            <div class="decisions-container">
              <div class="decision-row">
                <span class="decision-label">勝投</span>
                <span class="decision-value">{w_pitcher_str}</span>
              </div>
              <div class="decision-row">
                <span class="decision-label">敗投</span>
                <span class="decision-value">{l_pitcher_str}</span>
              </div>
              <div class="decision-row">
                <span class="decision-label">救援</span>
                <span class="decision-value">{s_pitcher_str}</span>
              </div>
            </div>
            {news_box_html}
            {game_tx_box_html}
          </div>

          <!-- Right Column: Key Performers Highlights -->
          <div class="highlights-section">
            <h3 class="highlights-header">單場焦點球員</h3>
            
            <!-- Batting Highlights -->
            <div class="performer-group">
              <span class="performer-category-title">打擊焦點</span>
              <div class="performer-list">
{batters_section_html}
              </div>
            </div>

            <!-- Pitching Highlights -->
            <div class="performer-group">
              <span class="performer-category-title">投手焦點</span>
              <div class="performer-list">
{pitchers_section_html}
              </div>
            </div>
          </div>
        </div>
      </div>
"""
        games_cards_html.append(card_html)
        
    # Build complete HTML by reading template
    template_path = "templates/report_template.html"
    if not os.path.exists(template_path):
        print(f"Error: Template file {template_path} not found.", file=sys.stderr)
        sys.exit(1)
        
    with open(template_path, "r", encoding="utf-8") as f:
        html_content = f.read()
        
    # Replace placeholders
    # Date formatting: e.g., 2026 年 07 月 19 日
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        date_formatted = dt.strftime("%Y 年 %m 月 %d 日")
    except:
        date_formatted = date_str
        
    html_content = html_content.replace("<!--DATE_PLACEHOLDER-->2026-07-19<!--/DATE_PLACEHOLDER-->", date_str)
    html_content = html_content.replace("<!--DATE_PLACEHOLDER-->2026 年 07 月 19 日<!--/DATE_PLACEHOLDER-->", date_formatted)
    
    # Overall summary block
    summary_panel_html = f"""<div class="summary-panel">
        <div class="summary-item">
          <span class="summary-label">總場數</span>
          <span class="summary-value highlight-blue">{total_games}</span>
        </div>
        <div class="summary-divider"></div>
        <div class="summary-item">
          <span class="summary-label">延賽場數</span>
          <span class="summary-value highlight-red">{postponed_count}</span>
        </div>
      </div>"""
    
    # We replace from <!--OVERALL_SUMMARY_PLACEHOLDER--> to <!--/OVERALL_SUMMARY_PLACEHOLDER-->
    start_tag = "<!--OVERALL_SUMMARY_PLACEHOLDER-->"
    end_tag = "<!--/OVERALL_SUMMARY_PLACEHOLDER-->"
    if start_tag in html_content and end_tag in html_content:
        pre = html_content.split(start_tag)[0]
        post = html_content.split(end_tag)[1]
        html_content = pre + start_tag + "\n      " + summary_panel_html + "\n      " + end_tag + post
        
    # Games list block
    start_g_tag = "<!--GAMES_LIST-->"
    end_g_tag = "<!--/GAMES_LIST-->"
    if start_g_tag in html_content and end_g_tag in html_content:
        pre_g = html_content.split(start_g_tag)[0]
        post_g = html_content.split(end_g_tag)[1]
        games_html = "\n".join(games_cards_html)
        html_content = pre_g + start_g_tag + "\n" + games_html + "\n      " + end_g_tag + post_g
        
    # Transactions section block
    start_tx_tag = "<!--TRANSACTIONS_SECTION-->"
    end_tx_tag = "<!--/TRANSACTIONS_SECTION-->"
    if start_tx_tag in html_content and end_tx_tag in html_content:
        pre_tx = html_content.split(start_tx_tag)[0]
        post_tx = html_content.split(end_tx_tag)[1]
        tx_section_html = build_transactions_section_html(daily_tx_list)
        html_content = pre_tx + start_tx_tag + "\n" + tx_section_html + "\n      " + end_tx_tag + post_tx
        
    # Output to hierarchical reports directory
    parts = date_str.split('-')
    year = parts[0]
    month = parts[1]
    output_dir = os.path.join("reports", year, month)
    os.makedirs(output_dir, exist_ok=True)
    report_output_path = os.path.join(output_dir, f"{date_str}.html")
    with open(report_output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"Successfully generated report: {report_output_path}")
    
    # Return metadata for index update
    summary_sentence = f"昨日共進行了 {total_games} 場賽事。"
    if postponed_count > 0:
        summary_sentence += f"其中有 {postponed_count} 場賽事延期。"
    if key_matchups_summary:
        summary_sentence += " " + "；".join(key_matchups_summary[:3]) + "。"
        
    return {
        "date": date_str,
        "total_games": total_games,
        "postponed": postponed_count,
        "summary": summary_sentence
    }

def update_index_page(new_metadata):
    metadata_path = "reports/metadata.json"
    metadata_db = {}
    
    # Read existing metadata
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata_db = json.load(f)
        except Exception as e:
            print(f"Warning: Could not read metadata.json ({e}). Creating new.", file=sys.stderr)
            
    # Update/Add new metadata
    metadata_db[new_metadata["date"]] = new_metadata
    
    # Save back
    try:
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata_db, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving metadata.json: {e}", file=sys.stderr)
        
    # Read index.html
    index_path = "index.html"
    if not os.path.exists(index_path):
        print(f"Error: index.html not found at root.", file=sys.stderr)
        return
        
    with open(index_path, "r", encoding="utf-8") as f:
        index_content = f.read()
        
    # Get sorted dates (newest first)
    sorted_dates = sorted(metadata_db.keys(), reverse=True)
    if not sorted_dates:
        return
        
    latest_date = sorted_dates[0]
    latest_meta = metadata_db[latest_date]
    
    # 1. Update LATEST_REPORT section
    try:
        dt = datetime.strptime(latest_date, "%Y-%m-%d")
        date_formatted = dt.strftime("%Y 年 %m 月 %d 日")
    except:
        date_formatted = latest_date
        
    parts_latest = latest_date.split('-')
    latest_path = f"reports/{parts_latest[0]}/{parts_latest[1]}/{latest_date}.html"
    latest_html = f"""<a href="{latest_path}" class="latest-card-anchor">
        <div class="latest-card">
          <span class="latest-tag">最新戰報</span>
          <div class="latest-date">{date_formatted}</div>
          <div class="latest-summary">{latest_meta['summary']}</div>
          <div class="latest-stats-row">
            <div class="stat-box">
              <span class="stat-label">總場數</span>
              <span class="stat-value blue">{latest_meta['total_games']}</span>
            </div>
            <div class="stat-box">
              <span class="stat-label">延賽場數</span>
              <span class="stat-value red">{latest_meta['postponed']}</span>
            </div>
          </div>
          <div class="open-btn-container">
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
            </svg>
          </div>
        </div>
      </a>"""
      
    start_tag = "<!-- LATEST_REPORT_START -->"
    end_tag = "<!-- /LATEST_REPORT_END -->"
    if start_tag in index_content and end_tag in index_content:
        pre = index_content.split(start_tag)[0]
        post = index_content.split(end_tag)[1]
        index_content = pre + start_tag + "\n      " + latest_html + "\n      " + end_tag + post
        
    # 2. Update REPORTS_LIST archive section
    archive_cards = []
    for date_str in sorted_dates:
        meta = metadata_db[date_str]
        parts_card = date_str.split('-')
        card_path = f"reports/{parts_card[0]}/{parts_card[1]}/{date_str}.html"
        card = f"""          <!-- History Item {date_str} -->
          <a href="{card_path}" class="history-card">
            <div>
              <div class="history-date">{date_str}</div>
              <div class="history-details">
                <span><span>總比賽場數：</span><strong>{meta['total_games']} 場</strong></span>
                <span><span>延賽場數：</span><strong>{meta['postponed']} 場</strong></span>
              </div>
            </div>
            <div class="history-link-text">
              開啟焦點戰報
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
              </svg>
            </div>
          </a>"""
        archive_cards.append(card)
        
    archive_html = "\n\n".join(archive_cards)
    start_a_tag = "<!-- REPORTS_LIST_START -->"
    end_a_tag = "<!-- REPORTS_LIST_END -->"
    if start_a_tag in index_content and end_a_tag in index_content:
        pre_a = index_content.split(start_a_tag)[0]
        post_a = index_content.split(end_a_tag)[1]
        index_content = pre_a + start_a_tag + "\n\n" + archive_html + "\n\n          " + end_a_tag + post_a
        
    # Save index.html
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index_content)
    print("Successfully updated index.html index page.")

def send_discord_notification(new_meta):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("Warning: DISCORD_WEBHOOK_URL env variable not set. Skipping Discord notification.")
        return
    
    date_str = new_meta["date"]
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        date_formatted = dt.strftime("%Y 年 %m 月 %d 日")
    except:
        date_formatted = date_str
        
    parts = date_str.split('-')
    report_url = f"https://haolun588.github.io/MLB-daily/reports/{parts[0]}/{parts[1]}/{date_str}.html"
    
    if new_meta.get("total_games", 0) == 0:
        description = "昨日無安排任何大聯盟例行賽事。"
        fields = []
    else:
        description = "昨日焦點戰報與最新賽況已生成，點擊下方連結即可瀏覽完整內容！"
        fields = [
            {
                "name": "📊 賽事統計",
                "value": f"• 總場數：**{new_meta['total_games']}** 場\n• 延賽場數：**{new_meta['postponed']}** 場",
                "inline": True
            },
            {
                "name": "📝 焦點摘要",
                "value": new_meta["summary"]
            }
        ]
        
    payload = {
        "embeds": [
            {
                "title": f"⚾ MLB 每日焦點戰報 - {date_formatted}",
                "description": description,
                "url": report_url,
                "color": 795456,  # Yankees Blue: #0C2340
                "fields": fields,
                "footer": {
                    "text": "MLB Daily 每日戰報系統"
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        ]
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            print(f"Discord notification sent successfully. Response code: {response.getcode()}")
    except Exception as e:
        print(f"Error sending Discord notification: {e}", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(description="MLB Daily Report Generator Script")
    parser.add_argument("--date", type=str, help="Target report date in YYYY-MM-DD format (defaults to yesterday in Taipei Time)")
    parser.add_argument("--check-wait", action="store_true", help="If active, loops and sleeps 30 minutes until yesterday's games are finished")
    
    args = parser.parse_args()
    
    # 1. Resolve date
    if args.date:
        date_str = args.date
        # Validate date format
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            print("Error: Date format must be YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)
    else:
        date_str = get_yesterday_str()
        
    print(f"Target Date: {date_str}")
    
    # 2. Check wait loop if enabled
    if args.check_wait:
        print("Enabling check wait loop. Monitoring yesterday's games...")
        while True:
            finalized, games = check_all_games_finalized(date_str)
            if finalized:
                print("All games are finalized (Final or Postponed). Proceeding to build.")
                break
            print("Some games are not finalized yet. Sleeping for 30 minutes...", flush=True)
            time.sleep(1800)
    else:
        finalized, games = check_all_games_finalized(date_str)
        if not finalized:
            print("Warning: Some games are still active or not finalized yet.", file=sys.stderr)
            
    # Check if there are games scheduled
    if not games:
        print(f"No games scheduled or found on {date_str}. Generating empty card/log.")
        # Create empty metadata to avoid breaking index page
        empty_meta = {
            "date": date_str,
            "total_games": 0,
            "postponed": 0,
            "summary": f"{date_str} 當日無安排任何大聯盟賽事。"
        }
        
        # Build empty card report html
        template_path = "templates/report_template.html"
        if os.path.exists(template_path):
            with open(template_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            html_content = html_content.replace("<!--DATE_PLACEHOLDER-->2026-07-19<!--/DATE_PLACEHOLDER-->", date_str)
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                date_formatted = dt.strftime("%Y 年 %m 月 %d 日")
            except:
                date_formatted = date_str
            html_content = html_content.replace("<!--DATE_PLACEHOLDER-->2026 年 07 年 19 日<!--/DATE_PLACEHOLDER-->", date_formatted)
            
            empty_card = f"""
      <div class="game-card">
        <div class="postponed-game-body">
          <div class="postponed-icon">📅</div>
          <div class="postponed-title">今日無賽事</div>
          <p>{date_str} 當日無安排任何大聯盟例行賽事。</p>
        </div>
      </div>
"""
            # Replace games list
            start_g = "<!--GAMES_LIST-->"
            end_g = "<!--/GAMES_LIST-->"
            if start_g in html_content and end_g in html_content:
                pre = html_content.split(start_g)[0]
                post = html_content.split(end_g)[1]
                html_content = pre + start_g + "\n" + empty_card + "\n      " + end_g + post
                
            # Replace transactions section
            daily_tx_list = fetch_daily_transactions(date_str)
            start_tx_tag = "<!--TRANSACTIONS_SECTION-->"
            end_tx_tag = "<!--/TRANSACTIONS_SECTION-->"
            if start_tx_tag in html_content and end_tx_tag in html_content:
                pre_tx = html_content.split(start_tx_tag)[0]
                post_tx = html_content.split(end_tx_tag)[1]
                tx_section_html = build_transactions_section_html(daily_tx_list)
                html_content = pre_tx + start_tx_tag + "\n" + tx_section_html + "\n      " + end_tx_tag + post_tx
                
            parts = date_str.split('-')
            year = parts[0]
            month = parts[1]
            output_dir = os.path.join("reports", year, month)
            os.makedirs(output_dir, exist_ok=True)
            report_output_path = os.path.join(output_dir, f"{date_str}.html")
            with open(report_output_path, "w", encoding="utf-8") as f:
                f.write(html_content)
                
        update_index_page(empty_meta)
        send_discord_notification(empty_meta)
        sys.exit(0)
        
    # 3. Generate report files
    new_meta = generate_report(date_str, games)
    
    # 4. Update index homepage listings
    update_index_page(new_meta)
    
    # 5. Send Discord notification
    send_discord_notification(new_meta)
    print("All tasks completed successfully.")

if __name__ == "__main__":
    main()
