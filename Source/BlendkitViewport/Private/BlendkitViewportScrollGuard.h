// macOS-only guard that stops trackpad/scroll-wheel events over Blendkit's
// external Qt windows from also driving the editor viewport camera. See the
// .mm for why Unreal needs this. No-op on other platforms (never compiled).
#pragma once

namespace BlendkitScrollGuard
{
	void Install();
	void Remove();
}
