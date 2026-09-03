// Exact-mouse-ray helper for the level editor viewport.
//
// Unreal's editor Python API has no public "mouse position in the active
// level viewport" call. This tiny Blueprint Function Library fills that one
// gap using the same code path Unreal's own gizmos/click-select use
// (FEditorViewportClient::CalcSceneView + FSceneView::DeprojectFVector2D), so
// it is exact across DPI, zoom, and ortho/perspective viewports - unlike the
// OS-cursor + assumed-FOV approximation used when this module isn't built
// (see bk_unreal/core/viewport_ue.py).
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "BlendkitViewportLibrary.generated.h"

UCLASS()
class UBlendkitViewportLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** Fills bValid + RayStart/RayEnd from the mouse position over the active
	 * level editor viewport, in world space. bValid is false if there is no
	 * active/hovered level viewport (e.g. focus is elsewhere).
	 *
	 * All three are out-params (no return value): Unreal's Python binding is
	 * ambiguous when a UFUNCTION mixes a bool return value with out-ref params
	 * (observed dropping the bool and returning only the 2 refs), so this
	 * avoids that entirely and always hands back a 3-tuple to Python. */
	UFUNCTION(BlueprintCallable, Category = "Blendkit|Viewport")
	static void GetMouseWorldRay(bool& bValid, FVector& RayStart, FVector& RayEnd);

	/** Fills bValid + OutUnitsPerPixel with how many world (cm) units one
	 * screen pixel covers at WorldLocation, in the viewport currently under
	 * the mouse cursor. Multiply a desired pixel width by this to get a
	 * world-space thickness that reads as constant-width on screen
	 * regardless of camera distance/zoom/FOV (debug-draw line thickness is
	 * otherwise in world units and becomes invisible at typical scene
	 * distances). bValid is false if there is no active/hovered level
	 * viewport. */
	UFUNCTION(BlueprintCallable, Category = "Blendkit|Viewport")
	static void GetWorldUnitsPerPixel(bool& bValid, const FVector& WorldLocation, float& OutUnitsPerPixel);

	/** Draws a filled, unlit triangle-soup mesh for one frame (Verts.Num()
	 * must be a multiple of 3; each consecutive triple is one triangle, no
	 * shared-vertex indexing). Wraps the engine's ``::DrawDebugMesh`` global
	 * directly rather than going through ``UKismetSystemLibrary``, whose
	 * Python binding does not expose an equivalent function. Used for the
	 * proxor hologram preview during drag-to-place. */
	UFUNCTION(BlueprintCallable, Category = "Blendkit|Viewport")
	static void DrawDebugTriangleMesh(UObject* WorldContextObject, const TArray<FVector>& Verts, const FLinearColor& Color, float Duration);
};
