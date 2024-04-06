import math
from abc import ABC, abstractmethod
from typing import Any, TypedDict, Literal, TypeVar

import discord
from discord.ext import commands
from rapidfuzz import fuzz

import breadcord


class _Unset:
    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "<unset>"

    def __str__(self) -> str:
        return "<unset>"


UNSET = _Unset()


class Page:
    def __init__(
        self,
        content: str | _Unset = UNSET,
        embed: discord.Embed | None = None,
        embeds: list[discord.Embed] | _Unset = UNSET,
        attachment: discord.Attachment | discord.File | None = None,
        attachments: list[discord.Attachment | discord.File] | _Unset = UNSET,
    ) -> None:
        self.content = content
        self.embeds = embeds
        self.attachments = attachments
        if embed is not None:
            if self.embeds is not UNSET:
                raise ValueError("Cannot set both `embed` and `embeds`.")
            self.embeds = [embed]
        if attachment is not None:
            if self.attachments is not UNSET:
                raise ValueError("Cannot set both `attachment` and `attachments`.")
            self.attachments = [attachment]

    def unpack(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not UNSET}


_T = TypeVar('_T')


class PaginatedView(discord.ui.View, ABC):
    """
    Example usage_
    .. code-block:: python

        view = ExamplePaginatedView(data)

        await ctx.reply(**(await view.get_page()).unpack(), view=view)

    """

    def __init__(self, data: list[_T], *, starting_index: int = 0, per_page: int = 1) -> None:
        super().__init__(timeout=60*60)
        self.data = data
        self.index = starting_index
        self.per_page = per_page

    def get_page_data(self) -> list[_T]:
        return self.data[self.index:self.index + self.per_page]

    @property
    def pages(self):
        return math.ceil(len(self.data) / self.per_page)

    @property
    def current_page(self):
        return self.index // self.per_page + 1

    @discord.ui.button(
        label="Previous",
        style=discord.ButtonStyle.grey,
        emoji="\N{BLACK LEFT-POINTING TRIANGLE}",
        disabled=True,
    )
    async def previous_page(self, interaction: discord.Interaction, _) -> None:
        self.index -= self.per_page
        self.update_buttons()
        await self.update_page(interaction)

    @discord.ui.button(
        label="Next",
        style=discord.ButtonStyle.grey,
        emoji="\N{BLACK RIGHT-POINTING TRIANGLE}",
    )
    async def next_page(self, interaction: discord.Interaction, _) -> None:
        self.index += self.per_page
        self.update_buttons()
        await self.update_page(interaction)

    def update_buttons(self) -> None:
        self.previous_page.disabled = self.index <= 0
        self.next_page.disabled = self.index >= len(self.data) - self.per_page

    async def update_page(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(view=self, **(await self.get_page()).unpack())

    @abstractmethod
    async def get_page(self) -> Page:
        ...


class Treatment(TypedDict):
    id: int
    label: str


class Experiment(TypedDict):
    kind: Literal["user", "guild"]
    id: str
    label: str
    file: str
    treatments: list[Treatment]


class DiscordFile(TypedDict):
    path: str
    tags: list[str]


class DiscordBuild(TypedDict):
    release_channels: dict[str, str]
    build_hash: str
    GLOBAL_ENV: dict[str, Any]
    build_date: str
    build_number: int
    db_created_at: str
    db_updated_at: str
    environment: Literal["production"] | str
    experiments: list[Experiment]
    files: list[DiscordFile]


class ExperimentEmbed(discord.Embed):
    def __init__(self, experiment: Experiment) -> None:
        super().__init__(
            title=experiment["label"],
            description="\n".join(s for s in (
                f"ID: `{experiment['id']}`",
                f"Kind: {experiment['kind'].title()}",
                f"File: `{experiment['file']}`",
            ) if s)
        )
        self.add_field(
            name="Treatments",
            value="\n".join(
                f"- {t}" for t in (
                    "Not Eligible", "Control Bucket", *(
                        f"Treatment {treatment['id']}: {treatment['label']}"
                        for treatment in experiment["treatments"]
                    )
                )
            )
        )


class ExperimentBrowserView(PaginatedView):
    def __init__(self, data: list[Experiment], **kwargs) -> None:
        super().__init__(
            sorted(data, key=self.sorter, reverse=True),
            per_page=20,
            **kwargs
        )

    @staticmethod
    def sorter(experiment: Experiment) -> int | str:
        experiment_id = experiment["id"][:len("yyyy-mm")]
        key = experiment_id.replace("-", "")
        return int(key) if key.isdigit() else key

    async def get_page(self) -> Page:
        experiments: list[Experiment] = self.get_page_data()
        embed = discord.Embed(
            title=f"Page {self.current_page}/{self.pages}",
            description="\n".join(
                (
                    f"- ({experiment['kind'].title()}) `{experiment['id']}`\n"
                    f"  - {discord.utils.escape_markdown(experiment['label'])}"
                )
                for experiment in experiments
            ),
        )
        return Page(embed=embed)


class DiscordData(breadcord.helpers.HTTPModuleCog):
    async def get_build(self, build_hash: str | None = None) -> DiscordBuild:
        if build_hash is None:
            async with self.session.get("https://discord.sale/api/builds/") as response:
                response.raise_for_status()
                build_hash = (await response.json())["builds"][0]["build_hash"]

        async with self.session.get(f"https://discord.sale/api/builds/{build_hash}") as response:
            response.raise_for_status()
            return await response.json()

    @commands.hybrid_command(aliases=["experiment", "exper"])
    async def experiments(
        self,
        ctx: commands.Context,
        *, experiment: str | None = None,
        build_hash: str | None = None
    ) -> None:
        build = await self.get_build(build_hash)

        if experiment:
            experiment = experiment.lower()
            for exp in build["experiments"]:
                if fuzz.partial_ratio(exp["label"].lower(), experiment) > 85:
                    break
                if fuzz.partial_ratio(exp["id"], experiment) > 85:
                    break
            else:
                await ctx.reply("No such experiment found.")
                return
            await ctx.reply(embed=ExperimentEmbed(exp))
            return

        view = ExperimentBrowserView(data=build["experiments"])
        await ctx.reply(**(await view.get_page()).unpack(), view=view)


async def setup(bot: breadcord.Bot):
    await bot.add_cog(DiscordData("discord_data"))
