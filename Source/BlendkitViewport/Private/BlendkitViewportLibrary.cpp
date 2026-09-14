#include "BlendkitViewportLibrary.h"

#include "Editor.h"
#include "EditorViewportClient.h"
#include "Engine/Engine.h"
#include "Engine/Canvas.h"
#include "CanvasItem.h"
#include "Debug/DebugDrawService.h"
#include "LevelEditorViewport.h"
#include "SceneView.h"
#include "UnrealClient.h"
#include "DrawDebugHelpers.h"

namespace
{
	struct FBlendkitDebugTextState
	{
		bool bEnabled = false;
		FVector WorldLocation = FVector::ZeroVector;
		FString Text;
		FLinearColor Color = FLinearColor::White;
	};

	FBlendkitDebugTextState GDebugTextState;
	bool GDebugTextServiceRegistered = false;
	bool GPlacementRealtimeActive = false;

	const FText& PlacementRealtimeOverrideName()
	{
		static const FText Name = FText::FromString(TEXT("BlendkitPlacement"));
		return Name;
	}

	// UDebugDrawService::Draw is called from FEditorViewportClient::Draw for
	// every level viewport (editor AND PIE) each frame, unlike
	// ::DrawDebugString (which only iterates World->GetPlayerControllerIterator
	// and is therefore a no-op with no PlayerController/HUD, i.e. outside PIE).
	void DrawBlendkitDebugText(UCanvas* Canvas, APlayerController*)
	{
		if (!GDebugTextState.bEnabled || Canvas == nullptr || GDebugTextState.Text.IsEmpty())
		{
			return;
		}
		const FVector ScreenPos = Canvas->Project(GDebugTextState.WorldLocation);
		if (ScreenPos.X < 0.0 || ScreenPos.Y < 0.0 || ScreenPos.X > Canvas->ClipX || ScreenPos.Y > Canvas->ClipY)
		{
			return; // off-screen or behind the camera
		}
		FCanvasTextItem TextItem(FVector2D::ZeroVector, FText::FromString(GDebugTextState.Text), GEngine->GetSmallFont(), GDebugTextState.Color);
		TextItem.EnableShadow(FLinearColor::Black);
		TextItem.Scale = FVector2D(1.25f, 1.25f);
		Canvas->DrawItem(TextItem, ScreenPos.X, ScreenPos.Y);
	}

	// GetActiveViewport() is the last-*focused* viewport, not necessarily the
	// one currently under the cursor - during a drag the OS focus is on the
	// external Qt asset-bar window, so that heuristic picks the wrong (or a
	// stale) viewport. Scan all level viewports and use whichever one's
	// client-space mouse position is actually within its own bounds.
	FEditorViewportClient* FindHoveredLevelViewport(FIntPoint& OutMousePos)
	{
		if (GEditor == nullptr)
		{
			return nullptr;
		}
		for (FEditorViewportClient* Client : GEditor->GetLevelViewportClients())
		{
			if (Client == nullptr || Client->Viewport == nullptr)
			{
				continue;
			}
			FIntPoint Pos;
			Client->Viewport->GetMousePos(Pos); // UE 5.8: void - just fills Pos.
			const FIntPoint Size = Client->Viewport->GetSizeXY();
			if (Pos.X >= 0 && Pos.Y >= 0 && Pos.X < Size.X && Pos.Y < Size.Y)
			{
				OutMousePos = Pos;
				return Client;
			}
		}
		return nullptr;
	}
}

void UBlendkitViewportLibrary::GetMouseWorldRay(bool& bValid, FVector& RayStart, FVector& RayEnd)
{
	bValid = false;

	FIntPoint MousePos = FIntPoint::ZeroValue;
	FEditorViewportClient* ViewportClient = FindHoveredLevelViewport(MousePos);
	if (ViewportClient == nullptr || ViewportClient->Viewport == nullptr)
	{
		return;
	}
	FViewport* Viewport = ViewportClient->Viewport;

	FSceneViewFamilyContext ViewFamily(
		FSceneViewFamily::ConstructionValues(Viewport, ViewportClient->GetScene(), ViewportClient->EngineShowFlags)
			.SetRealtimeUpdate(ViewportClient->IsRealtime()));
	FSceneView* View = ViewportClient->CalcSceneView(&ViewFamily);
	if (View == nullptr)
	{
		return;
	}

	FVector Origin;
	FVector Direction;
	View->DeprojectFVector2D(FVector2D(MousePos.X, MousePos.Y), Origin, Direction);

	RayStart = Origin;
	RayEnd = Origin + Direction * 1000000.0; // 10 km, comfortably past any level
	bValid = true;
}

void UBlendkitViewportLibrary::GetWorldUnitsPerPixel(bool& bValid, const FVector& WorldLocation, float& OutUnitsPerPixel)
{
	bValid = false;
	OutUnitsPerPixel = 1.0f;

	FIntPoint MousePos = FIntPoint::ZeroValue;
	FEditorViewportClient* ViewportClient = FindHoveredLevelViewport(MousePos);
	if (ViewportClient == nullptr || ViewportClient->Viewport == nullptr)
	{
		return;
	}
	FViewport* Viewport = ViewportClient->Viewport;

	FSceneViewFamilyContext ViewFamily(
		FSceneViewFamily::ConstructionValues(Viewport, ViewportClient->GetScene(), ViewportClient->EngineShowFlags)
			.SetRealtimeUpdate(ViewportClient->IsRealtime()));
	FSceneView* View = ViewportClient->CalcSceneView(&ViewFamily);
	if (View == nullptr)
	{
		return;
	}

	// Project WorldLocation and a 1-unit offset along the camera's right
	// vector; the on-screen pixel distance between them is pixels-per-cm at
	// that depth, which we invert to get the desired cm-per-pixel.
	const FVector RightVector = ViewportClient->GetViewRotation().RotateVector(FVector::RightVector);
	const FVector4 ScreenA = View->WorldToScreen(WorldLocation);
	const FVector4 ScreenB = View->WorldToScreen(WorldLocation + RightVector);
	FVector2D PixelA;
	FVector2D PixelB;
	if (!View->ScreenToPixel(ScreenA, PixelA) || !View->ScreenToPixel(ScreenB, PixelB))
	{
		return;
	}

	const float PixelsPerUnit = FVector2D::Distance(PixelA, PixelB);
	if (PixelsPerUnit > KINDA_SMALL_NUMBER)
	{
		OutUnitsPerPixel = 1.0f / PixelsPerUnit;
	}
	bValid = true;
}

void UBlendkitViewportLibrary::DrawDebugTriangleMesh(UObject* WorldContextObject, const TArray<FVector>& Verts, const FLinearColor& Color, float Duration)
{
	UWorld* World = GEngine ? GEngine->GetWorldFromContextObject(WorldContextObject, EGetWorldErrorMode::ReturnNull) : nullptr;
	const int32 NumTris = Verts.Num() / 3;
	if (World == nullptr || NumTris <= 0)
	{
		return;
	}

	TArray<int32> Indices;
	Indices.Reserve(NumTris * 3);
	for (int32 i = 0; i < NumTris * 3; ++i)
	{
		Indices.Add(i);
	}

	::DrawDebugMesh(World, Verts, Indices, Color.ToFColor(true), false, Duration, SDPG_World);
}

void UBlendkitViewportLibrary::DrawDebugTextWorld(bool bEnabled, const FVector& WorldLocation, const FString& Text, const FLinearColor& Color)
{
	if (!GDebugTextServiceRegistered)
	{
		// "Game" (the category originally used here) gates on the "is this a
		// game viewport" show flag, which is OFF in the plain editor level
		// viewport by design - registering under it never actually broadcasts
		// there, which was the bug ("no text" even though this function was
		// definitely being called). "StaticMeshes" is on in effectively every
		// real viewing session (turning it off would hide the whole level),
		// in both the editor viewport and PIE.
		UDebugDrawService::Register(TEXT("StaticMeshes"), FDebugDrawDelegate::CreateStatic(&DrawBlendkitDebugText));
		GDebugTextServiceRegistered = true;
	}
	GDebugTextState.bEnabled = bEnabled;
	GDebugTextState.WorldLocation = WorldLocation;
	GDebugTextState.Text = Text;
	GDebugTextState.Color = Color;
}

void UBlendkitViewportLibrary::SetPlacementRealtimeOverride(bool bEnabled)
{
	if (GEditor == nullptr || bEnabled == GPlacementRealtimeActive)
	{
		return;
	}
	for (FEditorViewportClient* Client : GEditor->GetLevelViewportClients())
	{
		if (Client == nullptr)
		{
			continue;
		}
		if (bEnabled)
		{
			Client->AddRealtimeOverride(true, PlacementRealtimeOverrideName());
		}
		else
		{
			Client->RemoveRealtimeOverride(PlacementRealtimeOverrideName(), /*bCheckMissingOverride=*/false);
		}
	}
	GPlacementRealtimeActive = bEnabled;
}
