// Python <-> Slate seam for the native Blendkit asset bar prototype.
//
// The Slate UI (SBlendkitAssetBar) is pure presentation; all data and actions
// still flow through the existing engine-agnostic Python core (client_lib,
// search, placement, bookmarks). This UObject is the bridge:
//
//   * Slate -> Python : dynamic multicast delegates the Python glue binds to
//     (search requested, drag started, bookmark toggled, ...). Slate broadcasts
//     them; Python reacts using the same core/ code the Qt UI uses.
//   * Python -> Slate : BlueprintCallable UFUNCTIONs Python calls to push
//     results / thumbnails / status. These fire native (C++) multicast
//     delegates the Slate widget listens to.
//
// Everything here must be touched on the game thread only (Slate + UObject);
// the Python glue marshals background client callbacks onto the Slate tick
// before calling in.
#pragma once

#include "CoreMinimal.h"
#include "UObject/Object.h"
#include "BlendkitBridge.generated.h"

USTRUCT(BlueprintType)
struct FBlendkitAssetItem
{
	GENERATED_BODY()

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString AssetId;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString Name;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString AssetType;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	bool bCanDownload = true;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	bool bBookmarked = false;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString ThumbnailPath;
};

// Slate -> Python.
DECLARE_DYNAMIC_MULTICAST_DELEGATE_TwoParams(FBlendkitSearchRequested, const FString&, Query, const FString&, AssetType);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FBlendkitAssetAction, const FString&, AssetId);
DECLARE_DYNAMIC_MULTICAST_DELEGATE(FBlendkitSimpleAction);

// Python -> Slate (native, not reflected).
DECLARE_MULTICAST_DELEGATE(FBlendkitResultsChanged);
DECLARE_MULTICAST_DELEGATE_TwoParams(FBlendkitThumbnailChanged, const FString& /*AssetId*/, const FString& /*Path*/);
DECLARE_MULTICAST_DELEGATE_OneParam(FBlendkitStatusChanged, const FString& /*Text*/);

UCLASS(BlueprintType)
class UBlendkitBridge : public UObject
{
	GENERATED_BODY()

public:
	/** Process-wide singleton (root-kept). */
	UFUNCTION(BlueprintCallable, Category = "Blendkit")
	static UBlendkitBridge* Get();

	/** Open (or focus) the native asset-bar dockable tab. */
	UFUNCTION(BlueprintCallable, Category = "Blendkit")
	void OpenTab();

	/** Replace the current result set (called from Python after a search). */
	UFUNCTION(BlueprintCallable, Category = "Blendkit")
	void SetResults(const TArray<FBlendkitAssetItem>& InItems);

	/** Update one tile's thumbnail once the client has downloaded it. */
	UFUNCTION(BlueprintCallable, Category = "Blendkit")
	void SetThumbnail(const FString& AssetId, const FString& Path);

	/** Set the status-line text (e.g. "Searching…", "42 results"). */
	UFUNCTION(BlueprintCallable, Category = "Blendkit")
	void SetStatus(const FString& Text);

	// Slate -> Python (Python binds via add_callable).
	UPROPERTY(BlueprintAssignable, Category = "Blendkit")
	FBlendkitSearchRequested OnSearchRequested;

	UPROPERTY(BlueprintAssignable, Category = "Blendkit")
	FBlendkitAssetAction OnAssetDragStarted;

	UPROPERTY(BlueprintAssignable, Category = "Blendkit")
	FBlendkitAssetAction OnAssetActivated;

	UPROPERTY(BlueprintAssignable, Category = "Blendkit")
	FBlendkitAssetAction OnBookmarkToggled;

	// Fired when the grid is scrolled near the end (request the next page).
	UPROPERTY(BlueprintAssignable, Category = "Blendkit")
	FBlendkitSimpleAction OnLoadMoreRequested;

	// Python -> Slate (Slate binds in C++).
	FBlendkitResultsChanged OnResultsChanged;
	FBlendkitThumbnailChanged OnThumbnailChanged;
	FBlendkitStatusChanged OnStatusChanged;

	const TArray<FBlendkitAssetItem>& GetResults() const { return Results; }
	const FString& GetStatus() const { return Status; }

private:
	UPROPERTY()
	TArray<FBlendkitAssetItem> Results;

	FString Status;
};
