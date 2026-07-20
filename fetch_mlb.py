#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

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

def generate_report(date_str, games):
    print(f"Generating report for {date_str} with {len(games)} games...")
    total_games = len(games)
    postponed_count = 0
    games_cards_html = []
    
    # Simple summary log for index metadata
    key_matchups_summary = []
    
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
            name, stats = find_pitcher_stats(dec_obj["winner"]["id"])
            if name:
                w_pitcher_str = f"{name} ({stats.get('wins', 0)}-{stats.get('losses', 0)})"
            else:
                w_pitcher_str = dec_obj["winner"]["fullName"]
                
        if "loser" in dec_obj:
            name, stats = find_pitcher_stats(dec_obj["loser"]["id"])
            if name:
                l_pitcher_str = f"{name} ({stats.get('wins', 0)}-{stats.get('losses', 0)})"
            else:
                l_pitcher_str = dec_obj["loser"]["fullName"]
                
        if "save" in dec_obj:
            name, stats = find_pitcher_stats(dec_obj["save"]["id"])
            if name:
                s_pitcher_str = f"{name} ({stats.get('saves', 0)})"
            else:
                s_pitcher_str = f"{dec_obj['save']['fullName']} ({stats.get('saves', 0)})"
                
        # Parse Player Highlights (Notable Performers)
        notable_batters = []
        notable_pitchers = []
        
        # Loop teams
        for team_type, team_abbr in [("away", away_info["abbr"]), ("home", home_info["abbr"])]:
            team_players = boxscore["teams"][team_type]["players"]
            for p_id, p_info in team_players.items():
                p_name = p_info["person"]["fullName"]
                pos = p_info.get("position", {}).get("abbreviation", "")
                
                # Batter check
                bat_stats = p_info.get("stats", {}).get("batting", {})
                if bat_stats and bat_stats.get("atBats", 0) > 0:
                    ab = bat_stats.get("atBats", 0)
                    r = bat_stats.get("runs", 0)
                    h = bat_stats.get("hits", 0)
                    rbi = bat_stats.get("rbi", 0)
                    bb = bat_stats.get("baseOnBalls", 0)
                    so = bat_stats.get("strikeOuts", 0)
                    hr = bat_stats.get("homeRuns", 0)
                    sb = bat_stats.get("stolenBases", 0)
                    
                    # Highlight criteria: HR > 0, hits >= 2, rbi >= 2, sb > 0, or combination
                    score = hr * 4.0 + sb * 2.0 + rbi * 1.5 + h * 1.0 + r * 0.5
                    if score >= 1.5 or hr > 0 or sb > 0:
                        # Find cumulative season stats
                        season_bat = p_info.get("seasonStats", {}).get("batting", {})
                        season_hr = season_bat.get("homeRuns", 0)
                        season_sb = season_bat.get("stolenBases", 0)
                        
                        notable_batters.append({
                            "name": p_name,
                            "team": team_abbr,
                            "pos": pos,
                            "ab": ab, "r": r, "h": h, "rbi": rbi, "bb": bb, "so": so,
                            "hr": hr, "sb": sb,
                            "season_hr": season_hr, "season_sb": season_sb,
                            "score": score
                        })
                        
                # Pitcher check
                pit_stats = p_info.get("stats", {}).get("pitching", {})
                if pit_stats and pit_stats.get("inningsPitched"):
                    ip = pit_stats.get("inningsPitched", "0.0")
                    h = pit_stats.get("hits", 0)
                    r = pit_stats.get("runs", 0)
                    er = pit_stats.get("earnedRuns", 0)
                    bb = pit_stats.get("baseOnBalls", 0)
                    so = pit_stats.get("strikeOuts", 0)
                    num_pitches = pit_stats.get("numberOfPitches", 0)
                    strikes = pit_stats.get("strikes", 0)
                    
                    is_starter = p_info.get("gameStatus", {}).get("isCurrentPitcher", False) or p_info.get("seasonStats", {}).get("pitching", {}).get("gamesStarted", 0) > 0
                    
                    # Check if decision pitcher
                    wins = pit_stats.get("wins", 0)
                    losses = pit_stats.get("losses", 0)
                    saves = pit_stats.get("saves", 0)
                    
                    # Notable criteria: wins > 0 (W), saves > 0 (S), starter, or high strikeouts
                    is_notable = wins > 0 or saves > 0 or so >= 4 or (is_starter and float(ip) >= 5.0 and er <= 3)
                    if is_notable:
                        season_pit = p_info.get("seasonStats", {}).get("pitching", {})
                        s_wins = season_pit.get("wins", 0)
                        s_losses = season_pit.get("losses", 0)
                        s_saves = season_pit.get("saves", 0)
                        
                        tag = ""
                        if wins > 0:
                            tag = f'<span class="performer-tag pitcher-win">W ({s_wins}-{s_losses})</span>'
                        elif saves > 0:
                            tag = f'<span class="performer-tag pitcher-win">S ({s_saves})</span>'
                        elif losses > 0:
                            tag = f'<span class="performer-tag" style="background: rgba(239, 68, 68, 0.1); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.2);">L ({s_wins}-{s_losses})</span>'
                        elif is_starter:
                            tag = '<span class="performer-tag" style="background: rgba(255, 255, 255, 0.05); color: var(--text-secondary); border: 1px solid var(--card-border);">Starter</span>'
                        else:
                            tag = '<span class="performer-tag" style="background: rgba(255, 255, 255, 0.05); color: var(--text-secondary); border: 1px solid var(--card-border);">Relief</span>'
                            
                        notable_pitchers.append({
                            "name": p_name,
                            "team": team_abbr,
                            "pos": pos,
                            "ip": ip, "h": h, "r": r, "er": er, "bb": bb, "so": so,
                            "pitches": num_pitches, "strikes": strikes,
                            "tag": tag,
                            "is_starter": is_starter,
                            "is_decision": wins > 0 or losses > 0 or saves > 0
                        })
                        
        # Sort notable lists
        notable_batters.sort(key=lambda x: x["score"], reverse=True)
        # Pitchers order: Wins first, Saves second, Losses third, then Starters, then Relievers
        def pitcher_sort_val(x):
            if "W (" in x["tag"]: return 0
            if "S (" in x["tag"]: return 1
            if "L (" in x["tag"]: return 2
            if x["is_starter"]: return 3
            return 4
        notable_pitchers.sort(key=pitcher_sort_val)
        
        # Pick top batters (up to 4) and pitchers (up to 3)
        selected_batters = notable_batters[:4]
        selected_pitchers = notable_pitchers[:3]
        
        # Build batters HTML list
        batters_html_list = []
        for b in selected_batters:
            tags = []
            if b["hr"] > 0:
                tags.append(f'<span class="performer-tag hr">HR: {format_cumulative(b["hr"], b["season_hr"])}</span>')
            if b["sb"] > 0:
                tags.append(f'<span class="performer-tag sb">SB: {format_cumulative(b["sb"], b["season_sb"])}</span>')
                
            tags_str = " ".join(tags)
            
            batter_card = f"""              <!-- Batter Highlight: {b['name']} -->
              <div class="performer-item">
                <div class="performer-name-row">
                  <div>
                    <span class="performer-name">{b['name']}</span>
                    <span class="performer-team">{b['team']} · {b['pos']}</span>
                  </div>
                  {tags_str}
                </div>
                <div class="performer-stats">
                  單場表現：打數 <span class="performer-stats-numbers">{b['ab']}</span> | 得分 <span class="performer-stats-numbers">{b['r']}</span> | 安打 <span class="performer-stats-numbers">{b['h']}</span> | 打點 <span class="performer-stats-numbers">{b['rbi']}</span> | 保送 <span class="performer-stats-numbers">{b['bb']}</span>
                </div>
              </div>"""
            batters_html_list.append(batter_card)
            
        # Build pitchers HTML list
        pitchers_html_list = []
        for p in selected_pitchers:
            pitcher_card = f"""              <!-- Pitcher Highlight: {p['name']} -->
              <div class="performer-item">
                <div class="performer-name-row">
                  <div>
                    <span class="performer-name">{p['name']}</span>
                    <span class="performer-team">{p['team']} · {p['pos']}</span>
                  </div>
                  {p['tag']}
                </div>
                <div class="performer-stats">
                  投球內容：局數 <span class="performer-stats-numbers">{p['ip']}</span> | 被安打 <span class="performer-stats-numbers">{p['h']}</span> | 失分 <span class="performer-stats-numbers">{p['r']}</span> | 自責 <span class="performer-stats-numbers">{p['er']}</span> | 保送 <span class="performer-stats-numbers">{p['bb']}</span> | 三振 <span class="performer-stats-numbers">{p['so']}</span> (球數 <span class="performer-stats-numbers">{p['pitches']}/{p['strikes']}</span>)
                </div>
              </div>"""
            pitchers_html_list.append(pitcher_card)
            
        batters_section_html = "\n".join(batters_html_list) if batters_html_list else "              <div style='color:var(--text-muted);font-size:0.85rem;padding:0.5rem;'>今日無突出打擊表現。</div>"
        pitchers_section_html = "\n".join(pitchers_html_list) if pitchers_html_list else "              <div style='color:var(--text-muted);font-size:0.85rem;padding:0.5rem;'>今日無突出投手表現。</div>"
        
        # Log summary sentence for index.html description
        if selected_batters:
            top_b = selected_batters[0]
            summary_sentence = f"{away_info['abbr']} @ {home_info['abbr']}，比分 {away_r}:{home_r}"
            if top_b["hr"] > 0:
                summary_sentence += f"，{top_b['name']} 擊出全壘打"
            key_matchups_summary.append(summary_sentence)
            
        # Build Game Card HTML
        card_html = f"""
      <!-- Game Card: {away_info['abbr']} @ {home_info['abbr']} -->
      <div class="game-card">
        <!-- Left Column: Matchup & Scores -->
        <div class="game-summary-section">
          <div>
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
                      <div class="team-badge" style="border-color: {away_info['color']}; color: {away_info['color']}; background: {away_info['color']}10;">{away_info['abbr']}</div>
                      <span>{away_info['name']} {away_rec_str}</span>
                    </td>
                    <td class="num-cell runs-cell">{away_r}</td>
                    <td class="num-cell">{away_h}</td>
                    <td class="num-cell">{away_e}</td>
                  </tr>
                  <tr class="{home_winner_class}">
                    <td class="team-cell">
                      <div class="team-badge" style="border-color: {home_info['color']}; color: {home_info['color']}; background: {home_info['color']}10;">{home_info['abbr']}</div>
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

          <!-- Pitching Decisions -->
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
        </div>

        <!-- Right Column: Key Performers Highlights -->
        <div class="highlights-section">
          <div>
            <h3 class="highlights-header">單場焦點球員</h3>
          </div>
          
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
        
    # Output to reports directory
    os.makedirs("reports", exist_ok=True)
    report_output_path = f"reports/{date_str}.html"
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
        
    latest_html = f"""<a href="reports/{latest_date}.html" class="latest-card-anchor">
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
        card = f"""          <!-- History Item {date_str} -->
          <a href="reports/{date_str}.html" class="history-card">
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
      <div class="game-card" style="grid-template-columns: 1fr;">
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
                
            os.makedirs("reports", exist_ok=True)
            with open(f"reports/{date_str}.html", "w", encoding="utf-8") as f:
                f.write(html_content)
                
        update_index_page(empty_meta)
        sys.exit(0)
        
    # 3. Generate report files
    new_meta = generate_report(date_str, games)
    
    # 4. Update index homepage listings
    update_index_page(new_meta)
    print("All tasks completed successfully.")

if __name__ == "__main__":
    main()
