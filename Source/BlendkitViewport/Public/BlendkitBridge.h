// Python <-> Slate bridge for the native Blendkit editor UI.
//
// The Slate UI (SBlendkitAssetBar) is pure presentation; all data and actions
// still flow through the existing engine-agnostic Python core (client_lib,
// search, placement, bookmarks). This UObject is the bridge:
//
//   * Slate -> Python : dynamic multicast delegates the Python glue binds to
//     (search requested, drag started, bookmark toggled, ...). Slate broadcasts
//     them; Python reacts using the engine-agnostic core.
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

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString Description;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString AuthorName;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString AuthorId;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString License;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString Price;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString Rating;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString DownloadCount;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString Tags;
};

USTRUCT(BlueprintType)
struct FBlendkitSettings
{
	GENERATED_BODY()

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString GlobalDir;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString BlenderExecutable;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString Resolution = TEXT("ORIGINAL");

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	bool bVerifySsl = true;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	bool bLoggedIn = false;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	bool bLoginPending = false;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	bool bBlenderPending = false;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString AccountStatus;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString BlenderStatus;

	UPROPERTY(BlueprintReadWrite, Category = "Blendkit")
	FString Message;
};

// Slate -> Python.
DECLARE_DYNAMIC_MULTICAST_DELEGATE_TwoParams(FBlendkitSearchRequested, const FString&, Query, const FString&, AssetType);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FBlendkitAssetAction, const FString&, AssetId);
DECLARE_DYNAMIC_MULTICAST_DELEGATE(FBlendkitSimpleAction);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FBlendkitSettingsAction, const FBlendkitSettings&, Settings);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FBlendkitTextAction, const FString&, Text);

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

	UFUNCTION(BlueprintCallable, Category = "Blendkit")
	void OpenSettingsTab();

	UFUNCTION(BlueprintCallable, Category = "Blendkit")
	void SetSettings(const FBlendkitSettings& InSettings);

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

	UPROPERTY(BlueprintAssignable, Category = "Blendkit")
	FBlendkitSimpleAction OnSettingsRequested;

	UPROPERTY(BlueprintAssignable, Category = "Blendkit")
	FBlendkitSettingsAction OnSettingsSaved;

	UPROPERTY(BlueprintAssignable, Category = "Blendkit")
	FBlendkitSimpleAction OnLoginRequested;

	UPROPERTY(BlueprintAssignable, Category = "Blendkit")
	FBlendkitSimpleAction OnLogoutRequested;

	UPROPERTY(BlueprintAssignable, Category = "Blendkit")
	FBlendkitTextAction OnManualKeyRequested;

	UPROPERTY(BlueprintAssignable, Category = "Blendkit")
	FBlendkitTextAction OnBlenderDetectRequested;

	// Python -> Slate (Slate binds in C++).
	FBlendkitResultsChanged OnResultsChanged;
	FBlendkitThumbnailChanged OnThumbnailChanged;
	FBlendkitStatusChanged OnStatusChanged;

	const TArray<FBlendkitAssetItem>& GetResults() const { return Results; }
	const FString& GetStatus() const { return Status; }
	const FBlendkitSettings& GetSettings() const { return Settings; }

	static void AddReferencedObjects(UObject* InThis, FReferenceCollector& Collector);

private:
	UPROPERTY()
	FBlendkitSettings Settings;

	UPROPERTY()
	TArray<FBlendkitAssetItem> Results;

	UPROPERTY(Transient)
	TArray<TObjectPtr<UObject>> BoundCallables;

	FString Status;
};
