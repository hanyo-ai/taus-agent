"""Element operations - CDP-based DOM element interactions.

Abstracted from browser_use's actor/element.py.
Provides click, type, scroll-into-view, and information retrieval for DOM elements.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from cdp_use.cdp.dom.commands import (
        DescribeNodeParameters,
        FocusParameters,
        GetAttributesParameters,
        GetBoxModelParameters,
        PushNodesByBackendIdsToFrontendParameters,
        ResolveNodeParameters,
    )
    from cdp_use.cdp.input.commands import DispatchMouseEventParameters
    from cdp_use.cdp.input.types import MouseButton
    from cdp_use.cdp.runtime.commands import CallFunctionOnParameters

    from .session import BrowserSession

ModifierType = Literal['Alt', 'Control', 'Meta', 'Shift']


class Element:
    """DOM element operations via CDP using BackendNodeId.

    Works with elements obtained from Page.query_selector() or
    Page.get_element(backend_node_id).
    """

    def __init__(
        self,
        browser_session: BrowserSession,
        backend_node_id: int,
        session_id: str | None = None,
    ):
        self._browser_session = browser_session
        self._client = browser_session.cdp_client
        self._backend_node_id = backend_node_id
        self._session_id = session_id

    async def _get_node_id(self) -> int:
        """Resolve backend node ID to DOM node ID."""
        params: PushNodesByBackendIdsToFrontendParameters = {'backendNodeIds': [self._backend_node_id]}
        result = await self._client.send.DOM.pushNodesByBackendIdsToFrontend(
            params, session_id=self._session_id
        )
        return result['nodeIds'][0]

    async def _get_remote_object_id(self) -> str | None:
        """Get the JavaScript remote object ID for this element."""
        node_id = await self._get_node_id()
        params: ResolveNodeParameters = {'nodeId': node_id}
        result = await self._client.send.DOM.resolveNode(params, session_id=self._session_id)
        return result['object'].get('objectId')

    async def _get_clickable_center(self) -> tuple[float, float]:
        """Find a clickable point at the center of the element."""
        # Method 1: Try getContentQuads (best for inline/complex layouts)
        try:
            result = await self._client.send.DOM.getContentQuads(
                params={'backendNodeId': self._backend_node_id},
                session_id=self._session_id,
            )
            quads = result.get('quads', [])
            if quads:
                quad = quads[0]  # First visual quad
                # quad: [x1, y1, x2, y2, x3, y3, x4, y4]
                x = sum(quad[0::2]) / 4
                y = sum(quad[1::2]) / 4
                return x, y
        except Exception:
            pass

        # Method 2: Fall back to getBoxModel
        try:
            result = await self._client.send.DOM.getBoxModel(
                params={'backendNodeId': self._backend_node_id},
                session_id=self._session_id,
            )
            model = result.get('model', {})
            content = model.get('content', [])
            if len(content) >= 8:
                x = (content[0] + content[2] + content[4] + content[6]) / 4
                y = (content[1] + content[3] + content[5] + content[7]) / 4
                return x, y
        except Exception:
            pass

        # Method 3: JavaScript getBoundingClientRect
        try:
            obj_id = await self._get_remote_object_id()
            if obj_id:
                bounds_result = await self._client.send.Runtime.callFunctionOn(
                    params={
                        'functionDeclaration': """
                            function() {
                                const rect = this.getBoundingClientRect();
                                return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};
                            }
                        """,
                        'objectId': obj_id,
                        'returnByValue': True,
                    },
                    session_id=self._session_id,
                )
                value = bounds_result.get('result', {}).get('value', {})
                if value:
                    return value['x'], value['y']
        except Exception:
            pass

        raise RuntimeError(f'Could not find clickable center for element {self._backend_node_id}')

    async def click(
        self,
        button: MouseButton = 'left',
        click_count: int = 1,
        modifiers: list[ModifierType] | None = None,
    ) -> None:
        """Click the element.

        Tries multiple methods to find a clickable point (content quads,
        box model, getBoundingClientRect), then dispatches mouse events.
        """
        _ = modifiers  # Not used currently but reserved for future
        x, y = await self._get_clickable_center()

        # Dispatch mouse events
        press_params: DispatchMouseEventParameters = {
            'type': 'mousePressed',
            'x': x,
            'y': y,
            'button': button,
            'clickCount': click_count,
        }
        await self._client.send.Input.dispatchMouseEvent(press_params, session_id=self._session_id)

        release_params: DispatchMouseEventParameters = {
            'type': 'mouseReleased',
            'x': x,
            'y': y,
            'button': button,
            'clickCount': click_count,
        }
        await self._client.send.Input.dispatchMouseEvent(release_params, session_id=self._session_id)

    async def double_click(self) -> None:
        """Double-click the element."""
        await self.click(click_count=2)

    async def right_click(self) -> None:
        """Right-click the element."""
        await self.click(button='right')

    async def hover(self) -> None:
        """Hover the mouse over this element."""
        x, y = await self._get_clickable_center()
        await self._client.send.Input.dispatchMouseEvent(
            params={'type': 'mouseMoved', 'x': x, 'y': y},
            session_id=self._session_id,
        )

    async def focus(self) -> None:
        """Focus the element."""
        node_id = await self._get_node_id()
        await self._client.send.DOM.focus(
            params={'nodeId': node_id},
            session_id=self._session_id,
        )

    async def type(self, text: str, clear: bool = True) -> None:
        """Type text into this element.

        Args:
            text: Text to type.
            clear: Whether to clear existing text first.
        """
        # Focus the element first
        await self.focus()

        if clear:
            # Select all existing text
            obj_id = await self._get_remote_object_id()
            if obj_id:
                await self._client.send.Runtime.callFunctionOn(
                    params={
                        'functionDeclaration': 'function() { this.select(); }',
                        'objectId': obj_id,
                    },
                    session_id=self._session_id,
                )

        # Type each character
        for char in text:
            await self._client.send.Input.dispatchKeyEvent(
                params={'type': 'char', 'text': char},
                session_id=self._session_id,
            )

    async def press(self, key: str) -> None:
        """Press a key on this element (e.g., 'Enter', 'Tab')."""
        await self.focus()
        await self._client.send.Input.dispatchKeyEvent(
            params={'type': 'keyDown', 'key': key},
            session_id=self._session_id,
        )
        await self._client.send.Input.dispatchKeyEvent(
            params={'type': 'keyUp', 'key': key},
            session_id=self._session_id,
        )

    async def scroll_into_view(self) -> None:
        """Scroll the element into view."""
        obj_id = await self._get_remote_object_id()
        if obj_id:
            await self._client.send.Runtime.callFunctionOn(
                params={
                    'functionDeclaration': 'function() { this.scrollIntoView({behavior: "instant", block: "center"}); }',
                    'objectId': obj_id,
                },
                session_id=self._session_id,
            )

    async def get_attribute(self, name: str) -> str | None:
        """Get an attribute value."""
        node_id = await self._get_node_id()
        try:
            result = await self._client.send.DOM.getAttributes(
                params={'nodeId': node_id},
                session_id=self._session_id,
            )
            attrs = result.get('attributes', [])
            for i in range(0, len(attrs), 2):
                if attrs[i] == name:
                    return attrs[i + 1]
        except Exception:
            pass
        return None

    async def get_attributes(self) -> dict[str, str]:
        """Get all attributes."""
        node_id = await self._get_node_id()
        try:
            result = await self._client.send.DOM.getAttributes(
                params={'nodeId': node_id},
                session_id=self._session_id,
            )
            attrs = result.get('attributes', [])
            return {attrs[i]: attrs[i + 1] for i in range(0, len(attrs), 2)}
        except Exception:
            return {}

    async def get_text(self) -> str:
        """Get the text content of this element."""
        obj_id = await self._get_remote_object_id()
        if obj_id:
            result = await self._client.send.Runtime.callFunctionOn(
                params={
                    'functionDeclaration': 'function() { return this.textContent || ""; }',
                    'objectId': obj_id,
                    'returnByValue': True,
                },
                session_id=self._session_id,
            )
            return str(result.get('result', {}).get('value', ''))
        return ''

    async def get_inner_html(self) -> str:
        """Get inner HTML."""
        obj_id = await self._get_remote_object_id()
        if obj_id:
            result = await self._client.send.Runtime.callFunctionOn(
                params={
                    'functionDeclaration': 'function() { return this.innerHTML || ""; }',
                    'objectId': obj_id,
                    'returnByValue': True,
                },
                session_id=self._session_id,
            )
            return str(result.get('result', {}).get('value', ''))
        return ''

    async def get_tag_name(self) -> str:
        """Get the tag name."""
        obj_id = await self._get_remote_object_id()
        if obj_id:
            result = await self._client.send.Runtime.callFunctionOn(
                params={
                    'functionDeclaration': 'function() { return this.tagName.toLowerCase(); }',
                    'objectId': obj_id,
                    'returnByValue': True,
                },
                session_id=self._session_id,
            )
            return str(result.get('result', {}).get('value', '')).lower()
        return ''

    async def get_bounding_box(self) -> dict | None:
        """Get the element's bounding box in page coordinates."""
        try:
            result = await self._client.send.DOM.getBoxModel(
                params={'backendNodeId': self._backend_node_id},
                session_id=self._session_id,
            )
            content = result.get('model', {}).get('content', [])
            if len(content) >= 8:
                return {
                    'x': min(content[0], content[2], content[4], content[6]),
                    'y': min(content[1], content[3], content[5], content[7]),
                    'width': max(content[0], content[2], content[4], content[6]) - min(content[0], content[2], content[4], content[6]),
                    'height': max(content[1], content[3], content[5], content[7]) - min(content[1], content[3], content[5], content[7]),
                }
        except Exception:
            pass

        # Fallback to JavaScript
        obj_id = await self._get_remote_object_id()
        if obj_id:
            result = await self._client.send.Runtime.callFunctionOn(
                params={
                    'functionDeclaration': """
                        function() {
                            const rect = this.getBoundingClientRect();
                            return {x: rect.left, y: rect.top, width: rect.width, height: rect.height};
                        }
                    """,
                    'objectId': obj_id,
                    'returnByValue': True,
                },
                session_id=self._session_id,
            )
            return result.get('result', {}).get('value')

        return None

    async def is_visible(self) -> bool:
        """Check if the element is visible."""
        try:
            # Get bounding box - if it's zero-sized, it's not visible
            box = await self.get_bounding_box()
            if not box or box['width'] <= 0 or box['height'] <= 0:
                return False

            # Check visibility via JS
            obj_id = await self._get_remote_object_id()
            if obj_id:
                result = await self._client.send.Runtime.callFunctionOn(
                    params={
                        'functionDeclaration': """
                            function() {
                                const style = window.getComputedStyle(this);
                                return style.display !== 'none' &&
                                       style.visibility !== 'hidden' &&
                                       style.opacity !== '0';
                            }
                        """,
                        'objectId': obj_id,
                        'returnByValue': True,
                    },
                    session_id=self._session_id,
                )
                return bool(result.get('result', {}).get('value', False))
        except Exception:
            pass
        return False

    async def is_enabled(self) -> bool:
        """Check if the element is enabled."""
        disabled = await self.get_attribute('disabled')
        return disabled is None

    async def select_option(self, value: str | None = None, label: str | None = None) -> None:
        """Select an option in a <select> element by value or label."""
        tag = await self.get_tag_name()
        if tag != 'select':
            raise RuntimeError(f'Element is a <{tag}>, not a <select>')

        obj_id = await self._get_remote_object_id()
        if not obj_id:
            return

        if value is not None:
            script = f"function() {{ this.value = {value!r}; this.dispatchEvent(new Event('change', {{bubbles: true}})); }}"
        elif label is not None:
            script = f"""
                function() {{
                    const options = Array.from(this.options);
                    const option = options.find(o => o.text === {label!r} || o.label === {label!r});
                    if (option) {{ this.value = option.value; this.dispatchEvent(new Event('change', {{bubbles: true}})); }}
                }}
            """
        else:
            raise ValueError('Either value or label must be provided')

        await self._client.send.Runtime.callFunctionOn(
            params={'functionDeclaration': script, 'objectId': obj_id},
            session_id=self._session_id,
        )

    async def upload_file(self, *file_paths: str) -> None:
        """Upload files to a file input element."""
        tag = await self.get_tag_name()
        type_attr = await self.get_attribute('type')
        if tag != 'input' or type_attr != 'file':
            raise RuntimeError(f'Element is not a file input (<{tag} type="{type_attr}">)')

        import os

        resolved = [os.path.abspath(p) for p in file_paths]
        for p in resolved:
            if not os.path.exists(p):
                raise FileNotFoundError(f'File not found: {p}')

        node_id = await self._get_node_id()
        await self._client.send.DOM.setFileInputFiles(
            params={'nodeId': node_id, 'files': resolved},
            session_id=self._session_id,
        )

    async def screenshot_element(
        self, path: str | None = None, format: str = 'png', quality: int | None = None
    ) -> bytes:
        """Take a screenshot of this specific element."""
        import base64

        box = await self.get_bounding_box()
        if not box:
            raise RuntimeError('Cannot screenshot element - no bounding box')

        params: dict = {
            'format': format,
            'clip': {'x': box['x'], 'y': box['y'], 'width': box['width'], 'height': box['height'], 'scale': 1},
            'captureBeyondViewport': True,
        }
        if quality and format == 'jpeg':
            params['quality'] = quality

        result = await self._client.send.Page.captureScreenshot(params=params, session_id=self._session_id)
        data = base64.b64decode(result['data'])

        if path:
            from pathlib import Path

            Path(path).write_bytes(data)

        return data
