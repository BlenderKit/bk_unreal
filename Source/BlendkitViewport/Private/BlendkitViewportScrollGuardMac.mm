#include "BlendkitViewportScrollGuard.h"

#if PLATFORM_MAC

// MacSystemIncludes.h brings in Cocoa/AppKit under UE's `FVector` workaround
// (Carbon defines its own `struct FVector`, which otherwise clashes with
// UE::Math::FVector).
#include "Mac/MacSystemIncludes.h"

// Why this exists
// ---------------
// Unreal's FMacApplication installs one process-wide local NSEvent monitor
// (NSEventMaskAny) and, in FMacApplication::ProcessScrollWheelEvent, forwards
// EVERY scroll / trackpad event to Slate's currently-hovered widget - the
// level viewport - without checking which window the event actually belongs
// to. So a two-finger scroll over Blendkit's external Qt asset-bar window
// also orbits/moves the editor camera. Qt calling event->accept() cannot help:
// Unreal observes the raw NSEvent through its own monitor, independently of
// whatever Qt does with it.
//
// This monitor runs before Unreal's (AppKit invokes local monitors in reverse
// registration order, and ours is installed later, at module startup). For a
// scroll event targeting one of our windows - any window that is NOT an
// FCocoaWindow, i.e. not a native Unreal Slate window - we deliver it straight
// to that window's view (so Qt still scrolls) and return nil, which drops it
// from the event chain before Unreal's monitor ever sees it. Real Unreal
// windows are passed through untouched.

static id GBlendkitScrollMonitor = nil;

void BlendkitScrollGuard::Install()
{
	if (GBlendkitScrollMonitor != nil)
	{
		return;
	}
	GBlendkitScrollMonitor = [NSEvent addLocalMonitorForEventsMatchingMask:NSEventMaskScrollWheel
		handler:^NSEvent* (NSEvent* Event)
		{
			NSWindow* Window = [Event window];
			if (Window == nil)
			{
				return Event;
			}
			Class CocoaWindowClass = NSClassFromString(@"FCocoaWindow");
			if (CocoaWindowClass != nil && [Window isKindOfClass:CocoaWindowClass])
			{
				return Event;
			}
			[[Window contentView] scrollWheel:Event];
			return nil;
		}];
}

void BlendkitScrollGuard::Remove()
{
	if (GBlendkitScrollMonitor != nil)
	{
		[NSEvent removeMonitor:GBlendkitScrollMonitor];
		GBlendkitScrollMonitor = nil;
	}
}

#endif // PLATFORM_MAC
