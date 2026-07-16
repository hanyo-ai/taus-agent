"""Browser session - CDP-based browser management without event bus.

Abstracted from browser_use's browser/session.py and session_manager.py.
Provides direct async API for launching, connecting, and controlling a Chromium-based browser.
No event bus, no watchdogs, no LLM integration - just pure browser automation.
"""

import asyncio
import base64
import logging
import platform
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import httpx
import psutil
from cdp_use import CDPClient
from cdp_use.cdp.network import Cookie
from cdp_use.cdp.target import AttachedToTargetEvent, SessionID, TargetID

from .config import BrowserConfig, find_chrome_executable, CHROME_DEBUG_PORT

logger = logging.getLogger(__name__)


def _find_free_port() -> int:
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


def _is_new_tab_page(url: str) -> bool:
    """Check if URL is a new tab page."""
    return (
        url.startswith('chrome://new-tab-page/')
        or url.startswith('chrome://newtab/')
        or url == 'chrome://newtab'
        or url == 'about:blank'
    )


class Target:
    """Browser target (page, iframe, worker)."""

    def __init__(self, target_id: TargetID, target_type: str = 'page', url: str = 'about:blank', title: str = ''):
        self.target_id = target_id
        self.target_type = target_type
        self.url = url
        self.title = title


class CDPSession:
    """CDP communication channel to a target."""

    def __init__(self, cdp_client: CDPClient, target_id: TargetID, session_id: SessionID):
        self.cdp_client = cdp_client
        self.target_id = target_id
        self.session_id = session_id
        self._lifecycle_events: list[dict] = []


class BrowserSession:
    """Direct CDP-based browser session.

    Minimal, synchronous-style async API. No event bus, no watchdogs.

    Usage:
        # Local browser
        config = BrowserConfig(headless=False)
        session = BrowserSession(config=config)
        await session.start()
        await session.navigate("https://example.com")
        page = await session.get_current_page()
        # ... do things ...
        await session.stop()

        # Connect to existing browser
        config = BrowserConfig(cdp_url="http://localhost:9222")
        session = BrowserSession(config=config)
        await session.start()
    """

    def __init__(self, config: BrowserConfig | None = None):
        self.config = config or BrowserConfig()
        self.id: str = str(uuid4())

        # CDP state
        self._cdp_client_root: CDPClient | None = None
        self._targets: dict[TargetID, Target] = {}
        self._sessions: dict[SessionID, CDPSession] = {}
        self._target_sessions: dict[TargetID, set[SessionID]] = {}
        self._session_to_target: dict[SessionID, TargetID] = {}

        # Agent focus
        self.agent_focus_target_id: TargetID | None = None

        # Subprocess (local launch)
        self._browser_process: psutil.Process | None = None
        self._owns_browser: bool = False

        self._logger = logger

    # ========================================================================
    # Lifecycle
    # ========================================================================

    async def start(self) -> Self:
        """Start the browser session - launch or connect."""
        if self.config.cdp_url:
            # Connect to existing browser
            await self._connect(self.config.cdp_url)
        else:
            # Launch local browser
            self.config.is_local = True
            cdp_url = await self._launch_browser()
            await self._connect(cdp_url)

        return self

    async def stop(self) -> None:
        """Stop the browser session. Kills the browser if we launched it."""
        try:
            # Close CDP
            if self._cdp_client_root:
                try:
                    await self._cdp_client_root.stop()
                except Exception as e:
                    self._logger.debug(f'Error stopping CDP client: {e}')
                self._cdp_client_root = None

            # Kill browser if we own it
            if self._owns_browser and self._browser_process:
                try:
                    self._browser_process.kill()
                except Exception as e:
                    self._logger.debug(f'Error killing browser: {e}')
                self._browser_process = None
                self._owns_browser = False

            # Clear state
            self._targets.clear()
            self._sessions.clear()
            self._target_sessions.clear()
            self._session_to_target.clear()
            self.agent_focus_target_id = None

        except Exception as e:
            self._logger.warning(f'Error during browser stop: {e}')

    async def kill(self) -> None:
        """Force-kill the browser and clean up."""
        self._owns_browser = True  # Ensure we kill even if we didn't launch
        await self.stop()

    # ========================================================================
    # Internal: Browser Launch
    # ========================================================================

    async def _launch_browser(self) -> str:
        """Launch a local Chrome/Chromium browser and return CDP URL."""

        # Find executable
        executable_path = self.config.executable_path
        if not executable_path:
            executable_path = find_chrome_executable()
        if not executable_path:
            raise RuntimeError(
                'No Chrome/Chromium found. Install Chrome or set executable_path in BrowserConfig.'
            )

        executable_path = str(executable_path)

        # Ensure user_data_dir
        if not self.config.user_data_dir:
            if self.config.performance_mode:
                # Persistent profile for cache/cookies/DNS/TLS reuse
                self.config.user_data_dir = str(Path.home() / '.taus-browser-profile')
            else:
                self.config.user_data_dir = tempfile.mkdtemp(prefix='taus-browser-')

        # Build args
        debug_port = _find_free_port()
        launch_args = self.config.get_args()
        launch_args.extend([f'--remote-debugging-port={debug_port}'])

        self._logger.debug(f'🚀 Launching browser: {executable_path}')
        self._logger.debug(f'   Args: {" ".join(launch_args[:5])}... ({len(launch_args)} total)')
        self._logger.debug(f'   User data dir: {self.config.user_data_dir}')

        # Launch subprocess
        # On macOS ARM, force native arch to avoid Rosetta (x64 emulation is ~10x slower)
        if sys.platform == 'darwin' and platform.machine() == 'arm64':
            cmd = ['arch', '-arm64', executable_path, *launch_args]
        else:
            cmd = [executable_path, *launch_args]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._browser_process = psutil.Process(proc.pid)
        self._owns_browser = True

        self._logger.debug(f'   Browser PID: {proc.pid}, CDP port: {debug_port}')

        # Wait for CDP to be ready
        cdp_url = await self._wait_for_cdp_url(debug_port)
        self._logger.debug(f'   CDP ready: {cdp_url}')
        return cdp_url

    async def _wait_for_cdp_url(self, port: int, max_wait: float = 30.0) -> str:
        """Wait for Chrome's CDP endpoint to become available."""
        url = f'http://localhost:{port}'
        start = time.time()

        while time.time() - start < max_wait:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
                    resp = await client.get(f'{url}/json/version')
                    if resp.status_code == 200:
                        data = resp.json()
                        ws_url = data.get('webSocketDebuggerUrl', '')
                        if ws_url:
                            return ws_url
            except Exception:
                pass
            await asyncio.sleep(0.1)

        raise RuntimeError(f'Timed out waiting for CDP on port {port} after {max_wait}s')

    # ========================================================================
    # Internal: CDP Connection
    # ========================================================================

    async def _connect(self, cdp_url: str) -> None:
        """Connect to a Chromium-based browser via CDP."""

        # Resolve WebSocket URL if given HTTP URL
        if not cdp_url.startswith('ws'):
            parsed = urlparse(cdp_url)
            path = parsed.path.rstrip('/')
            if not path.endswith('/json/version'):
                path = path + '/json/version'
            http_url = urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment))

            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), trust_env=False) as client:
                headers = self.config.headers or {}
                resp = await client.get(http_url, headers=headers)
                cdp_url = resp.json()['webSocketDebuggerUrl']

        self._logger.debug(f'🔌 Connecting via CDP: {cdp_url}')

        # Create CDP client
        self._cdp_client_root = CDPClient(
            cdp_url,
            additional_headers=self.config.headers,
            max_ws_frame_size=200 * 1024 * 1024,  # 200MB for large DOMs
        )
        await self._cdp_client_root.start()

        # Enable auto-attach for target discovery
        await self._cdp_client_root.send.Target.setAutoAttach(
            params={'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True}
        )

        # Set up target tracking
        await self._setup_target_tracking()

        # Discover existing targets
        await self._discover_targets()

        # Ensure at least one page
        page_targets = self._get_page_targets()
        if not page_targets:
            target_id = await self._create_target('about:blank')
            self._logger.debug(f'📄 Created initial page: {target_id}')
        else:
            target_id = page_targets[0].target_id

        # Set initial focus
        await self._ensure_session(target_id)
        self.agent_focus_target_id = target_id
        self._logger.debug(f'🎯 Agent focus: {target_id[-8:]}...')

    async def _setup_target_tracking(self) -> None:
        """Register CDP event handlers for target tracking."""
        assert self._cdp_client_root is not None

        def _on_attached(event: AttachedToTargetEvent, session_id: SessionID | None = None):
            asyncio.ensure_future(self._handle_attached(event))

        def _on_detached(event: dict, session_id: SessionID | None = None):
            asyncio.ensure_future(self._handle_detached(event))

        def _on_target_info_changed(event: dict, session_id: SessionID | None = None):
            asyncio.ensure_future(self._handle_target_info_changed(event))

        # Enable target discovery
        await self._cdp_client_root.send.Target.setDiscoverTargets(
            params={'discover': True, 'filter': [{'type': 'page'}, {'type': 'iframe'}]}
        )

        # Register handlers
        try:
            self._cdp_client_root.register.Target.attachedToTarget(_on_attached)
        except Exception:
            pass
        try:
            self._cdp_client_root.register.Target.detachedFromTarget(_on_detached)
        except Exception:
            pass
        try:
            self._cdp_client_root.register.Target.targetInfoChanged(_on_target_info_changed)
        except Exception:
            pass

    async def _handle_attached(self, event: AttachedToTargetEvent) -> None:
        """Handle Target.attachedToTarget event."""
        target_id = event.get('targetId') or ''
        session_id = event.get('sessionId') or ''
        target_info = event.get('targetInfo', {})
        target_type = target_info.get('type', '')
        url = target_info.get('url', 'about:blank')
        title = target_info.get('title', '')

        if not target_id or not session_id:
            return

        # Auto-attach to child targets
        if target_type != 'browser':
            try:
                await self._cdp_client_root.send.Target.setAutoAttach(  # type: ignore[union-attr]
                    params={'autoAttach': True, 'waitForDebuggerOnStart': False, 'flatten': True},
                    session_id=session_id,
                )
            except Exception:
                pass

        # Create CDP session
        cdp_session = CDPSession(self._cdp_client_root, target_id, session_id)
        self._sessions[session_id] = cdp_session
        self._session_to_target[session_id] = target_id
        self._target_sessions.setdefault(target_id, set()).add(session_id)

        # Store target if new
        if target_id not in self._targets:
            target = Target(target_id=target_id, target_type=target_type, url=url, title=title)
            self._targets[target_id] = target

        # Enable domains for page targets
        if target_type == 'page':
            try:
                await asyncio.gather(
                    self._cdp_client_root.send.Page.enable(session_id=session_id),
                    self._cdp_client_root.send.DOM.enable(session_id=session_id),
                    self._cdp_client_root.send.Runtime.enable(session_id=session_id),
                    self._cdp_client_root.send.Network.enable(session_id=session_id),
                )
                # Enable lifecycle event tracking
                await self._cdp_client_root.send.Page.setLifecycleEventsEnabled(
                    params={'enabled': True}, session_id=session_id
                )

                def on_lifecycle(event_data, sid: SessionID | None = None):
                    sid = sid or session_id
                    if sid in self._sessions:
                        self._sessions[sid]._lifecycle_events.append(event_data)

                try:
                    self._cdp_client_root.register.Page.lifecycleEvent(on_lifecycle)
                except Exception:
                    pass

            except Exception as e:
                self._logger.debug(f'Error enabling domains: {e}')

    async def _handle_detached(self, event: dict) -> None:
        """Handle Target.detachedFromTarget event."""
        target_id = event.get('targetId', '')
        session_id = event.get('sessionId', '')
        if session_id in self._sessions:
            del self._sessions[session_id]
        if session_id in self._session_to_target:
            tid = self._session_to_target.pop(session_id)
            if tid in self._target_sessions:
                self._target_sessions[tid].discard(session_id)
                if not self._target_sessions[tid]:
                    del self._target_sessions[tid]
                    # Only remove non-page targets on detach
                    if tid in self._targets and self._targets[tid].target_type != 'page':
                        del self._targets[tid]

    async def _handle_target_info_changed(self, event: dict) -> None:
        """Handle Target.targetInfoChanged event."""
        target_info = event.get('targetInfo', {})
        target_id = target_info.get('targetId', '')
        if target_id in self._targets:
            self._targets[target_id].url = target_info.get('url', 'about:blank')
            self._targets[target_id].title = target_info.get('title', '')

    async def _discover_targets(self) -> None:
        """Discover all existing targets from the browser."""
        assert self._cdp_client_root is not None
        try:
            targets = await self._cdp_client_root.send.Target.getTargets()
            for t in targets.get('targetInfos', []):
                tid = t.get('targetId', '')
                if tid and tid not in self._targets:
                    self._targets[tid] = Target(
                        target_id=tid,
                        target_type=t.get('type', ''),
                        url=t.get('url', 'about:blank'),
                        title=t.get('title', ''),
                    )
        except Exception as e:
            self._logger.debug(f'Error discovering targets: {e}')

    async def _create_target(self, url: str = 'about:blank') -> TargetID:
        """Create a new page target."""
        assert self._cdp_client_root is not None
        result = await self._cdp_client_root.send.Target.createTarget(params={'url': url})
        return result['targetId']

    async def _close_target(self, target_id: TargetID) -> None:
        """Close a page target."""
        assert self._cdp_client_root is not None
        try:
            await self._cdp_client_root.send.Target.closeTarget(params={'targetId': target_id})
        except Exception as e:
            self._logger.debug(f'Error closing target: {e}')

    # ========================================================================
    # Internal: Session Management
    # ========================================================================

    async def _ensure_session(self, target_id: TargetID) -> CDPSession:
        """Get or create a CDP session for a target."""
        # Check existing session
        sids = self._target_sessions.get(target_id, set())
        for sid in sids:
            if sid in self._sessions:
                return self._sessions[sid]

        # Attach new session
        assert self._cdp_client_root is not None
        result = await self._cdp_client_root.send.Target.attachToTarget(
            params={'targetId': target_id, 'flatten': True}
        )
        session_id = result['sessionId']

        cdp_session = CDPSession(self._cdp_client_root, target_id, session_id)
        self._sessions[session_id] = cdp_session
        self._session_to_target[session_id] = target_id
        self._target_sessions.setdefault(target_id, set()).add(session_id)

        # Enable domains
        await asyncio.gather(
            self._cdp_client_root.send.Page.enable(session_id=session_id),
            self._cdp_client_root.send.DOM.enable(session_id=session_id),
            self._cdp_client_root.send.Runtime.enable(session_id=session_id),
            self._cdp_client_root.send.Network.enable(session_id=session_id),
        )

        return cdp_session

    async def _get_focused_session(self) -> CDPSession:
        """Get the CDP session for the currently focused target."""
        if not self.agent_focus_target_id:
            raise RuntimeError('No agent focus target set')
        return await self._ensure_session(self.agent_focus_target_id)

    def _get_page_targets(self) -> list[Target]:
        """Get all page/tab targets."""
        return [t for t in self._targets.values() if t.target_type == 'page']

    # ========================================================================
    # Public: High-Level API
    # ========================================================================

    @property
    def cdp_client(self) -> CDPClient:
        """Get the root CDP client."""
        if not self._cdp_client_root:
            raise RuntimeError('Not connected. Call start() first.')
        return self._cdp_client_root

    async def get_current_page(self):
        """Get the current page as a Page actor object."""
        from .page import Page

        if not self.agent_focus_target_id:
            raise RuntimeError('No focused target')
        return Page(self, self.agent_focus_target_id)

    async def get_pages(self) -> list['Page']:
        """Get all pages."""
        from .page import Page

        return [Page(self, t.target_id) for t in self._get_page_targets()]

    async def navigate(self, url: str, new_tab: bool = False, wait_until: str = 'load') -> None:
        """Navigate to a URL.

        Args:
            url: URL to navigate to.
            new_tab: Open in a new tab instead of current tab.
            wait_until: 'load', 'domcontentloaded', 'networkidle', or 'commit'.
        """
        target_id = self.agent_focus_target_id
        if not target_id:
            raise RuntimeError('No focused target')

        if new_tab:
            target_id = await self._create_target('about:blank')
            await self.switch_to_tab(target_id)

        await self._navigate_and_wait(url, target_id, wait_until=wait_until)

    async def _navigate_and_wait(
        self, url: str, target_id: TargetID, wait_until: str = 'load', timeout: float | None = None
    ) -> None:
        """Navigate and wait for page load."""
        cdp_session = await self._ensure_session(target_id)

        if timeout is None:
            target_url = self._targets.get(target_id, Target(target_id)).url
            same_domain = url.split('/')[2] == target_url.split('/')[2] if 'http' in url and 'http' in target_url else False
            timeout = 3.0 if same_domain else 8.0

        try:
            nav_result = await asyncio.wait_for(
                cdp_session.cdp_client.send.Page.navigate(
                    params={'url': url, 'transitionType': 'address_bar'},
                    session_id=cdp_session.session_id,
                ),
                timeout=20.0,
            )
        except TimeoutError:
            raise RuntimeError(f'Navigation timed out for {url}')

        if nav_result.get('errorText'):
            raise RuntimeError(f'Navigation failed: {nav_result["errorText"]}')

        if wait_until == 'commit':
            return

        # Poll document.readyState until target state is reached
        start = time.time()
        check_interval = 0.15

        while time.time() - start < timeout:
            try:
                ready_state = await asyncio.wait_for(
                    self.evaluate('document.readyState', target_id=target_id),
                    timeout=3.0,
                )
                if ready_state == 'complete':
                    return
                if wait_until == 'domcontentloaded' and ready_state in ('interactive', 'complete'):
                    return
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                self._logger.debug(f'readyState poll error: {e}')

            await asyncio.sleep(check_interval)

        self._logger.warning(f'Page readiness timeout ({timeout}s) for {url}')

    async def switch_to_tab(self, target_id: TargetID) -> None:
        """Switch focus to a specific tab."""
        cdp_session = await self._ensure_session(target_id)
        await cdp_session.cdp_client.send.Target.activateTarget(params={'targetId': target_id})
        self.agent_focus_target_id = target_id

    async def close_tab(self, target_id: TargetID | None = None) -> None:
        """Close a tab. If no target_id, close current tab."""
        if target_id is None:
            target_id = self.agent_focus_target_id
        if not target_id:
            return

        await self._close_target(target_id)

        # Switch to another tab if we closed the current one
        if self.agent_focus_target_id == target_id:
            pages = self._get_page_targets()
            if pages:
                await self.switch_to_tab(pages[0].target_id)
            else:
                self.agent_focus_target_id = None

    async def get_tabs(self) -> list[dict[str, Any]]:
        """Get all open tabs info."""
        tabs = []
        for t in self._get_page_targets():
            tabs.append({
                'target_id': t.target_id,
                'url': t.url,
                'title': t.title,
            })
        return tabs

    async def get_current_url(self) -> str:
        """Get the current page URL."""
        if self.agent_focus_target_id and self.agent_focus_target_id in self._targets:
            return self._targets[self.agent_focus_target_id].url
        return 'about:blank'

    async def get_current_title(self) -> str:
        """Get the current page title."""
        if self.agent_focus_target_id and self.agent_focus_target_id in self._targets:
            return self._targets[self.agent_focus_target_id].title
        return ''

    async def screenshot(self, path: str | None = None, full_page: bool = False,
                         format: str = 'png', quality: int | None = None,
                         clip: dict | None = None) -> bytes:
        """Take a screenshot.

        Args:
            path: Optional file path to save screenshot.
            full_page: Capture full scrollable page.
            format: 'png', 'jpeg', or 'webp'.
            quality: JPEG quality 0-100.
            clip: Region dict {'x','y','width','height'}.

        Returns:
            Screenshot bytes.
        """
        cdp_session = await self._get_focused_session()

        params: dict = {'format': format, 'captureBeyondViewport': full_page}
        if quality and format == 'jpeg':
            params['quality'] = quality
        if clip:
            params['clip'] = {'x': clip['x'], 'y': clip['y'], 'width': clip['width'], 'height': clip['height'], 'scale': 1}

        result = await cdp_session.cdp_client.send.Page.captureScreenshot(
            params=params, session_id=cdp_session.session_id
        )

        if not result or 'data' not in result:
            raise RuntimeError('Screenshot failed')

        data = base64.b64decode(result['data'])
        if path:
            Path(path).write_bytes(data)

        return data

    async def evaluate(self, expression: str, target_id: TargetID | None = None) -> Any:
        """Evaluate JavaScript in the current page."""
        if target_id is None:
            cdp_session = await self._get_focused_session()
        else:
            cdp_session = await self._ensure_session(target_id)

        result = await cdp_session.cdp_client.send.Runtime.evaluate(
            params={'expression': expression, 'returnByValue': True},
            session_id=cdp_session.session_id,
        )
        return result.get('result', {}).get('value')

    async def get_cookies(self) -> list[Cookie]:
        """Get all cookies."""
        cdp_session = await self._get_focused_session()
        result = await cdp_session.cdp_client.send.Storage.getCookies(session_id=cdp_session.session_id)
        return result.get('cookies', [])

    async def clear_cookies(self) -> None:
        """Clear all cookies."""
        cdp_session = await self._get_focused_session()
        await cdp_session.cdp_client.send.Storage.clearCookies(session_id=cdp_session.session_id)

    async def set_viewport(self, width: int, height: int, device_scale_factor: float = 1.0) -> None:
        """Set viewport size."""
        cdp_session = await self._get_focused_session()
        await cdp_session.cdp_client.send.Emulation.setDeviceMetricsOverride(
            params={
                'width': width,
                'height': height,
                'deviceScaleFactor': device_scale_factor,
                'mobile': False,
            },
            session_id=cdp_session.session_id,
        )
