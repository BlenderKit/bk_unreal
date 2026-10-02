// Native (Slate) Blendkit asset bar — a dockable editor panel prototype that
// mirrors the Qt asset bar (ui/asset_bar.py) but lives inside Unreal's own UI,
// so it shares the editor thread, event loop and input system (no Qt threading
// marshaling, no macOS scroll/event-leak hacks). Data + actions flow through
// UBlendkitBridge to the existing Python core.
#pragma once

#include "CoreMinimal.h"
#include "Widgets/SCompoundWidget.h"
#include "Widgets/Views/STileView.h"

class SSearchBox;
class SEditableTextBox;
class STextBlock;
struct FBlendkitAssetItem;

/** One tile's view-model: a copy of the bridge item plus its loaded brush. */
struct FBlendkitTileItem
{
	FString AssetId;
	FString Name;
	FString AssetType;
	bool bCanDownload = true;
	bool bBookmarked = false;
	FString ThumbnailPath;
	TSharedPtr<FSlateBrush> Brush;
};

class SBlendkitAssetBar : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SBlendkitAssetBar) {}
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs);
	virtual ~SBlendkitAssetBar() override;

	virtual void Tick(const FGeometry& AllottedGeometry, const double InCurrentTime, const float InDeltaTime) override;

private:
	void RebuildItems();
	void OnThumbnailChanged(const FString& AssetId, const FString& Path);
	void OnStatusChanged(const FString& Text);

	TSharedRef<class ITableRow> OnGenerateTile(TSharedPtr<FBlendkitTileItem> Item, const TSharedRef<STableViewBase>& Owner);
	void OnSearchCommitted(const FText& Text, ETextCommit::Type CommitType);
	void TriggerSearch();
	void OnListScrolled(double ScrollOffset);

	TArray<TSharedPtr<FString>> AssetTypes;
	TSharedPtr<FString> CurrentType;

	TArray<TSharedPtr<FBlendkitTileItem>> Items;
	TSharedPtr<STileView<TSharedPtr<FBlendkitTileItem>>> TileView;
	TSharedPtr<SSearchBox> SearchBox;
	TSharedPtr<STextBlock> StatusText;

	FDelegateHandle ResultsHandle;
	FDelegateHandle ThumbHandle;
	FDelegateHandle StatusHandle;

	// Last DPI/layout scale seen; a change (e.g. maximize/restore) forces the
	// tile grid to re-measure so text/thumbnails don't render at a stale scale.
	float LastLayoutScale = -1.f;
};
