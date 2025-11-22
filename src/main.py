import datetime

from wox_plugin import (
    ActionContext,
    Context,
    Plugin,
    PluginInitParams,
    PublicAPI,
    Query,
    RefreshableResult,
    Result,
    ResultAction,
    WoxImage,
    WoxImageType,
)


class MyPlugin(Plugin):
    api: PublicAPI

    async def init(self, ctx: Context, init_params: PluginInitParams) -> None:
        self.api = init_params.api

    def on_refresh(self, r: RefreshableResult) -> RefreshableResult:
        r.sub_title = f"Refresh at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        return r

    async def action(self, actionContext: ActionContext):
        await self.api.log(Context.new(), "info", actionContext.context_data)

    async def query(self, ctx: Context, query: Query) -> list[Result]:
        results: list[Result] = []
        search_term = query.search.lower() if query.search else ""

        results.append(
            Result(
                title=f"you typed {search_term}",
                sub_title="this is subsitle",
                icon=WoxImage(
                    image_type=WoxImageType.RELATIVE,
                    image_data="image/app.png",
                ),
                actions=[
                    ResultAction(
                        name="My Action",
                        prevent_hide_after_action=True,
                        action=self.action,
                    )
                ],
                refresh_interval=1000,
                on_refresh=self.on_refresh,
            )
        )

        return results


plugin = MyPlugin()
