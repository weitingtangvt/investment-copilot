"""Text"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box
from typing import List, Dict, Optional, Any
import time


class Display:
    """Text"""

    def __init__(self):
        self.console = Console()

    def clear(self):
        """Text"""
        self.console.clear()

    def print(self, message: str, style: str = None):
        """Text"""
        self.console.print(message, style=style)

    def print_markdown(self, content: str):
        """Text Markdown Text"""
        self.console.print(Markdown(content))

    def print_error(self, message: str):
        """TextError"""
        self.console.print(f"[red]Error: {message}[/red]")

    def print_success(self, message: str):
        """TextSuccess"""
        self.console.print(f"[green]{message}[/green]")

    def print_warning(self, message: str):
        """Text"""
        self.console.print(f"[yellow]{message}[/yellow]")

    def print_info(self, message: str):
        """Text"""
        self.console.print(f"[cyan]{message}[/cyan]")

    # ==================== Text ====================

    def panel(self, content: str, title: str = None, subtitle: str = None,
              border_style: str = "blue"):
        """Text"""
        self.console.print(Panel(
            content,
            title=title,
            subtitle=subtitle,
            border_style=border_style,
            box=box.ROUNDED
        ))

    def playbook_panel(self, playbook: Dict, is_portfolio: bool = False):
        """Text Playbook Text"""
        if is_portfolio:
            # Text Playbook
            content_lines = []

            market_views = playbook.get("market_views", {})

            # Text
            bullish = market_views.get("bullish_themes", [])
            if bullish:
                content_lines.append("[bold]Text:[/bold]")
                for theme in bullish:
                    if isinstance(theme, dict):
                        content_lines.append(f"  - {theme.get('theme', '')} ({theme.get('confidence', '')})")
                    else:
                        content_lines.append(f"  - {theme}")

            # Text
            bearish = market_views.get("bearish_themes", [])
            if bearish:
                content_lines.append("")
                content_lines.append("[bold]Text:[/bold]")
                for theme in bearish:
                    if isinstance(theme, dict):
                        content_lines.append(f"  - {theme.get('theme', '')}")
                    else:
                        content_lines.append(f"  - {theme}")

            # Text
            macro = market_views.get("macro_views", [])
            if macro:
                content_lines.append("")
                content_lines.append("[bold]Text:[/bold]")
                for view in macro:
                    content_lines.append(f"  - {view}")

            # Text
            strategy = playbook.get("portfolio_strategy", {})
            if strategy:
                content_lines.append("")
                content_lines.append("[bold]Text:[/bold]")
                allocation = strategy.get("target_allocation", {})
                for k, v in allocation.items():
                    content_lines.append(f"  - {k}: {v}")
                if strategy.get("risk_tolerance"):
                    content_lines.append(f"  - RiskText: {strategy['risk_tolerance']}")

            title = "Text Playbook"
            subtitle = f"Text: {playbook.get('updated_at', '')[:10]}"

        else:
            # Text Playbook
            content_lines = []

            core_thesis = playbook.get("core_thesis", {})
            content_lines.append(f"[bold]Text:[/bold] {core_thesis.get('summary', '')}")

            key_points = core_thesis.get("key_points", [])
            for point in key_points:
                content_lines.append(f"  - {point}")

            # Text
            triggers = playbook.get("invalidation_triggers", [])
            if triggers:
                content_lines.append("")
                content_lines.append("[bold]Text:[/bold]")
                for trigger in triggers:
                    content_lines.append(f"  - {trigger}")

            # Text
            plan = playbook.get("operation_plan", {})
            if plan:
                content_lines.append("")
                content_lines.append("[bold]Text:[/bold]")
                if plan.get("holding_period"):
                    content_lines.append(f"  - Text: {plan['holding_period']}")
                if plan.get("target_price"):
                    content_lines.append(f"  - Text: {plan['target_price']}")
                if plan.get("stop_loss"):
                    content_lines.append(f"  - Text: {plan['stop_loss']}")

            stock_name = playbook.get("stock_name", "")
            ticker = playbook.get("ticker", "")
            title = f"{stock_name} ({ticker}) - Text Playbook" if ticker else f"{stock_name} - Text Playbook"
            subtitle = f"Text: {playbook.get('updated_at', '')[:10]}"

        self.panel("\n".join(content_lines), title=title, subtitle=subtitle)

    def research_plan_panel(self, plan: Dict):
        """TextResearchText"""
        content_lines = []

        content_lines.append("[bold]Text:[/bold]")
        for i, q in enumerate(plan.get("core_questions", []), 1):
            content_lines.append(f"  {i}. {q}")

        content_lines.append("")
        content_lines.append("[bold]ResearchText:[/bold]")
        for dim in plan.get("research_dimensions", []):
            content_lines.append(f"  - {dim}")

        content_lines.append("")
        content_lines.append("[bold]Text:[/bold]")
        for src in plan.get("information_sources", []):
            content_lines.append(f"  - {src}")

        content_lines.append("")
        content_lines.append(f"[bold]SearchText:[/bold] {plan.get('search_time_range', '7d')}")

        self.panel("\n".join(content_lines), title="ResearchText(Text)", border_style="yellow")

    def environment_panel(self, auto_collected: List[Dict], user_uploaded: List[Dict]):
        """Text Environment Text"""
        content_lines = []

        if auto_collected:
            content_lines.append("[bold]AutoText:[/bold]")
            for item in auto_collected:
                date_str = item.get("date", "")
                title = item.get("title", "")
                content_lines.append(f"  - [{date_str}] {title}")

        if user_uploaded:
            if content_lines:
                content_lines.append("")
            content_lines.append("[bold]TextUpload:[/bold]")
            for item in user_uploaded:
                filename = item.get("filename", "")
                summary = item.get("summary", "")[:50]
                content_lines.append(f"  - {filename}: {summary}...")

        if not content_lines:
            content_lines.append("No dataText")

        self.panel("\n".join(content_lines), title="Environment TextSummary", border_style="cyan")

    def dimension_panel(self, dimension: int, title: str, content: Dict):
        """TextAnalysisText"""
        content_lines = []
        for k, v in content.items():
            if isinstance(v, list):
                content_lines.append(f"[bold]{k}:[/bold]")
                for item in v:
                    content_lines.append(f"  - {item}")
            else:
                content_lines.append(f"[bold]{k}:[/bold] {v}")

        self.panel("\n".join(content_lines), title=f"Text {dimension}: {title}", border_style="magenta")

    # ==================== Text ====================

    def stocks_table(self, stocks: List[Dict]):
        """TextStockText"""
        table = Table(title="TextHoldings", box=box.ROUNDED)
        table.add_column("Stock", style="cyan")
        table.add_column("Ticker", style="green")
        table.add_column("Text", style="white")
        table.add_column("Text", style="dim")

        for stock in stocks:
            table.add_row(
                stock.get("stock_name", stock.get("stock_id", "")),
                stock.get("ticker", ""),
                stock.get("summary", "")[:30] + "..." if len(stock.get("summary", "")) > 30 else stock.get("summary", ""),
                stock.get("updated_at", "")[:10]
            )

        self.console.print(table)

    def history_table(self, records: List[Dict]):
        """TextResearchHistory"""
        if not records:
            self.print_info("No dataResearchHistory")
            return

        table = Table(title="ResearchHistory", box=box.ROUNDED)
        table.add_column("Date", style="cyan")
        table.add_column("Text", style="white")
        table.add_column("Text", style="green")
        table.add_column("TextDecision", style="yellow")

        for record in records[:10]:  # Text 10 Text
            result = record.get("research_result", {})
            feedback = record.get("user_feedback", {})

            table.add_row(
                record.get("date", "")[:10],
                record.get("impact_assessment", {}).get("reason", "")[:30] + "...",
                result.get("recommendation", ""),
                feedback.get("final_decision", "")
            )

        self.console.print(table)

    # ==================== Text ====================

    def input(self, prompt: str = "> ") -> str:
        """Text"""
        return Prompt.ask(prompt)

    def confirm(self, message: str, default: bool = True) -> bool:
        """Confirm"""
        return Confirm.ask(message, default=default)

    def choice(self, message: str, choices: List[str]) -> str:
        """Text"""
        self.print(message)
        for i, choice in enumerate(choices, 1):
            self.print(f"  {i}. {choice}")
        while True:
            answer = self.input()
            if answer.isdigit():
                idx = int(answer) - 1
                if 0 <= idx < len(choices):
                    return choices[idx]
            elif answer in choices:
                return answer
            self.print_error("Text, Text")

    # ==================== Text ====================

    def spinner(self, message: str):
        """Text spinner Text"""
        return Progress(
            SpinnerColumn(),
            TextColumn(f"[cyan]{message}[/cyan]"),
            console=self.console,
            transient=True
        )

    def show_spinner(self, message: str, duration: float = 1.0):
        """Text spinner Text"""
        with self.spinner(message) as progress:
            progress.add_task("", total=None)
            time.sleep(duration)

    # ==================== Text ====================

    def separator(self):
        """Text"""
        self.console.print("━" * 50, style="dim")

    def header(self):
        """Text"""
        self.console.print()
        self.console.print("[bold blue]TextResearchText v2.0[/bold blue]")
        self.console.print('[dim]Text "Text" Text[/dim]')
        self.console.print()
