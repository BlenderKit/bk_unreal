#include "SBlendkitAssetBar.h"

#include "BlendkitBridge.h"

#include "Framework/Application/SlateApplication.h"
#include "Framework/MultiBox/MultiBoxBuilder.h"
#include "IImageWrapper.h"
#include "IImageWrapperModule.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "Rendering/SlateRenderer.h"
#include "Styling/CoreStyle.h"
#include "HAL/PlatformApplicationMisc.h"
#include "Widgets/Images/SImage.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SComboBox.h"
#include "Widgets/Input/SSearchBox.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Layout/SSpacer.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/SOverlay.h"
#include "Widgets/Text/STextBlock.h"
#include "Widgets/Views/STableRow.h"

#define LOCTEXT_NAMESPACE "Blendkit"

namespace
{
	constexpr float TileWidth = 130.f;
	constexpr float TileHeight = 152.f;

	/** Decode a downloaded thumbnail (png/jpg) into a Slate brush. webp may not
	 *  decode on all engine versions — returns nullptr and the tile stays blank. */
	TSharedPtr<FSlateBrush> MakeImageBrush(const FString& Path)
	{
		if (Path.IsEmpty() || !FPaths::FileExists(Path))
		{
			return nullptr;
		}

		TArray<uint8> FileData;
		if (!FFileHelper::LoadFileToArray(FileData, *Path) || FileData.Num() == 0)
		{
			return nullptr;
		}

		IImageWrapperModule& Module = FModuleManager::LoadModuleChecked<IImageWrapperModule>(TEXT("ImageWrapper"));
		const EImageFormat Format = Module.DetectImageFormat(FileData.GetData(), FileData.Num());
		if (Format == EImageFormat::Invalid)
		{
			return nullptr;
		}

		TSharedPtr<IImageWrapper> Wrapper = Module.CreateImageWrapper(Format);
		if (!Wrapper.IsValid() || !Wrapper->SetCompressed(FileData.GetData(), FileData.Num()))
		{
			return nullptr;
		}

		TArray<uint8> Raw;
		if (!Wrapper->GetRaw(ERGBFormat::BGRA, 8, Raw))
		{
			return nullptr;
		}

		const int32 Width = Wrapper->GetWidth();
		const int32 Height = Wrapper->GetHeight();
		if (Width <= 0 || Height <= 0)
		{
			return nullptr;
		}

		const FName ResourceName(*Path);
		if (!FSlateApplication::Get().GetRenderer()->GenerateDynamicImageResource(ResourceName, Width, Height, Raw))
		{
			return nullptr;
		}

		return MakeShareable(new FSlateDynamicImageBrush(ResourceName, FVector2D(Width, Height)));
	}
}

// ---------------------------------------------------------------------------
// Asset Detail Dialog
// ---------------------------------------------------------------------------

class SBlendkitAssetDetail : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SBlendkitAssetDetail) {}
		SLATE_ARGUMENT(TSharedPtr<FBlendkitTileItem>, Item)
		SLATE_ARGUMENT(TSharedPtr<SWindow>, ParentWindow)
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs)
	{
		Item = InArgs._Item;
		ParentWindow = InArgs._ParentWindow;

		TSharedRef<SVerticalBox> InfoBox = SNew(SVerticalBox);

		auto AddRow = [&InfoBox](const FText& Label, const FString& Value, const FSlateColor& Color = FSlateColor::UseForeground())
		{
			if (!Value.IsEmpty())
			{
				InfoBox->AddSlot()
				.AutoHeight()
				.Padding(0.f, 2.f)
				[
					SNew(SHorizontalBox)
					+ SHorizontalBox::Slot()
					.AutoWidth()
					[
						SNew(STextBlock)
						.Text(Label)
						.Font(FCoreStyle::GetDefaultFontStyle("Bold", 9))
						.ColorAndOpacity(FSlateColor(FLinearColor(0.6f, 0.6f, 0.6f)))
					]
					+ SHorizontalBox::Slot()
					.FillWidth(1.f)
					.Padding(8.f, 0.f, 0.f, 0.f)
					[
						SNew(STextBlock)
						.Text(FText::FromString(Value))
						.Font(FCoreStyle::GetDefaultFontStyle("Regular", 9))
						.ColorAndOpacity(Color)
						.AutoWrapText(true)
					]
				];
			}
		};

		const FString PriceText = Item.IsValid() && !Item->Price.IsEmpty() ? Item->Price : TEXT("—");
		const FSlateColor PriceColor = (PriceText == TEXT("FREE")) ? FSlateColor(FLinearColor(0.2f, 0.85f, 0.3f)) : FSlateColor::UseForeground();

		AddRow(LOCTEXT("DetailType", "Type:"), Item.IsValid() ? Item->AssetType.ToUpper() : TEXT(""));
		AddRow(LOCTEXT("DetailAuthor", "Author:"), Item.IsValid() ? Item->AuthorName : TEXT(""));
		AddRow(LOCTEXT("DetailPrice", "Price:"), PriceText, PriceColor);
		AddRow(LOCTEXT("DetailLicense", "License:"), Item.IsValid() ? Item->License : TEXT(""));
		AddRow(LOCTEXT("DetailRating", "Rating:"), Item.IsValid() && !Item->Rating.IsEmpty() ? FString::Printf(TEXT("\u2605 %s"), *Item->Rating) : TEXT(""));
		AddRow(LOCTEXT("DetailDownloads", "Downloads:"), Item.IsValid() ? Item->DownloadCount : TEXT(""));

		const FString Desc = Item.IsValid() && !Item->Description.IsEmpty() ? Item->Description : TEXT("No description provided.");

		ChildSlot
		[
			SNew(SBorder)
			.Padding(16.f)
			.BorderImage(FCoreStyle::Get().GetBrush("ToolPanel.GroupBorder"))
			[
				SNew(SVerticalBox)
				// Title
				+ SVerticalBox::Slot()
				.AutoHeight()
				.Padding(0.f, 0.f, 0.f, 12.f)
				[
					SNew(STextBlock)
					.Text(FText::FromString(Item.IsValid() ? Item->Name : TEXT("Asset Detail")))
					.Font(FCoreStyle::GetDefaultFontStyle("Bold", 14))
					.AutoWrapText(true)
				]
				// Header row: Large thumbnail + Quick info
				+ SVerticalBox::Slot()
				.AutoHeight()
				.Padding(0.f, 0.f, 0.f, 12.f)
				[
					SNew(SHorizontalBox)
					// Thumbnail
					+ SHorizontalBox::Slot()
					.AutoWidth()
					[
						SNew(SBox)
						.WidthOverride(180.f)
						.HeightOverride(180.f)
						[
							SNew(SBorder)
							.BorderBackgroundColor(FLinearColor(0.1f, 0.1f, 0.1f))
							.Padding(2.f)
							[
								SNew(SImage)
								.Image_Lambda([this]() -> const FSlateBrush*
								{
									return (Item.IsValid() && Item->Brush.IsValid()) ? Item->Brush.Get() : nullptr;
								})
							]
						]
					]
					// Info list
					+ SHorizontalBox::Slot()
					.FillWidth(1.f)
					.Padding(16.f, 0.f, 0.f, 0.f)
					[
						InfoBox
					]
				]
				// Description header
				+ SVerticalBox::Slot()
				.AutoHeight()
				.Padding(0.f, 6.f, 0.f, 4.f)
				[
					SNew(STextBlock)
					.Text(LOCTEXT("DescriptionHeading", "Description"))
					.Font(FCoreStyle::GetDefaultFontStyle("Bold", 10))
				]
				// Description box
				+ SVerticalBox::Slot()
				.FillHeight(1.f)
				.Padding(0.f, 0.f, 0.f, 8.f)
				[
					SNew(SBorder)
					.BorderBackgroundColor(FLinearColor(0.08f, 0.08f, 0.08f, 0.8f))
					.Padding(8.f)
					[
						SNew(SScrollBox)
						+ SScrollBox::Slot()
						[
							SNew(STextBlock)
							.Text(FText::FromString(Desc))
							.Font(FCoreStyle::GetDefaultFontStyle("Regular", 9))
							.ColorAndOpacity(FSlateColor(FLinearColor(0.85f, 0.85f, 0.85f)))
							.AutoWrapText(true)
						]
					]
				]
				// Tags
				+ SVerticalBox::Slot()
				.AutoHeight()
				.Padding(0.f, 0.f, 0.f, 12.f)
				[
					SNew(STextBlock)
					.Text(FText::FromString(Item.IsValid() && !Item->Tags.IsEmpty() ? FString::Printf(TEXT("Tags: %s"), *Item->Tags) : TEXT("")))
					.Font(FCoreStyle::GetDefaultFontStyle("Italic", 8))
					.ColorAndOpacity(FSlateColor(FLinearColor(0.6f, 0.6f, 0.6f)))
					.AutoWrapText(true)
				]
				// Action buttons
				+ SVerticalBox::Slot()
				.AutoHeight()
				[
					SNew(SHorizontalBox)
					+ SHorizontalBox::Slot()
					.AutoWidth()
					[
						SNew(SButton)
						.Text_Lambda([this]()
						{
							return (Item.IsValid() && Item->bBookmarked) ? LOCTEXT("DetailRemoveBookmark", "★ Bookmarked") : LOCTEXT("DetailAddBookmark", "☆ Add Bookmark");
						})
						.OnClicked_Lambda([this]()
						{
							if (Item.IsValid())
							{
								if (UBlendkitBridge* Bridge = UBlendkitBridge::Get())
								{
									Bridge->OnBookmarkToggled.Broadcast(Item->AssetId);
								}
								Item->bBookmarked = !Item->bBookmarked;
							}
							return FReply::Handled();
						})
					]
					+ SHorizontalBox::Slot()
					.AutoWidth()
					.Padding(8.f, 0.f, 0.f, 0.f)
					[
						SNew(SButton)
						.Visibility_Lambda([this]()
						{
							return (Item.IsValid() && !Item->AuthorName.IsEmpty()) ? EVisibility::Visible : EVisibility::Collapsed;
						})
						.Text(LOCTEXT("DetailAuthorSearch", "More by Author"))
						.OnClicked_Lambda([this]()
						{
							if (Item.IsValid())
							{
								if (UBlendkitBridge* Bridge = UBlendkitBridge::Get())
								{
									const FString Query = Item->AuthorId.IsEmpty() ? Item->AuthorName : FString::Printf(TEXT("author_id:%s"), *Item->AuthorId);
									Bridge->OnSearchRequested.Broadcast(Query, Item->AssetType);
								}
								if (TSharedPtr<SWindow> Win = ParentWindow.Pin())
								{
									Win->RequestDestroyWindow();
								}
							}
							return FReply::Handled();
						})
					]
					+ SHorizontalBox::Slot()
					.FillWidth(1.f)
					[
						SNew(SSpacer)
					]
					+ SHorizontalBox::Slot()
					.AutoWidth()
					.Padding(8.f, 0.f, 0.f, 0.f)
					[
						SNew(SButton)
						.Text(LOCTEXT("DetailOpenWeb", "View on Blendkit.com"))
						.OnClicked_Lambda([this]()
						{
							if (Item.IsValid())
							{
								if (UBlendkitBridge* Bridge = UBlendkitBridge::Get())
								{
									Bridge->OnAssetActivated.Broadcast(Item->AssetId);
								}
							}
							return FReply::Handled();
						})
					]
					+ SHorizontalBox::Slot()
					.AutoWidth()
					.Padding(8.f, 0.f, 0.f, 0.f)
					[
						SNew(SButton)
						.Text(LOCTEXT("DetailClose", "Close"))
						.OnClicked_Lambda([this]()
						{
							if (TSharedPtr<SWindow> Win = ParentWindow.Pin())
							{
								Win->RequestDestroyWindow();
							}
							return FReply::Handled();
						})
					]
				]
			]
		];
	}

private:
	TSharedPtr<FBlendkitTileItem> Item;
	TWeakPtr<SWindow> ParentWindow;
};

// ---------------------------------------------------------------------------
// Tile
// ---------------------------------------------------------------------------

class SBlendkitAssetTile : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SBlendkitAssetTile) {}
		SLATE_ARGUMENT(TSharedPtr<FBlendkitTileItem>, Item)
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs)
	{
		Item = InArgs._Item;

		ChildSlot
		[
			SNew(SBox)
			.WidthOverride(TileWidth)
			.HeightOverride(TileHeight)
			.Padding(2.f)
			[
				SNew(SBorder)
				.Padding(3.f)
				[
					SNew(SVerticalBox)
					+ SVerticalBox::Slot()
					.FillHeight(1.f)
					[
						SNew(SOverlay)
						+ SOverlay::Slot()
						[
							SNew(SImage)
							.Image_Lambda([this]() -> const FSlateBrush*
							{
								return (Item.IsValid() && Item->Brush.IsValid()) ? Item->Brush.Get() : nullptr;
							})
						]
						+ SOverlay::Slot()
						.HAlign(HAlign_Right)
						.VAlign(VAlign_Top)
						[
							SNew(SButton)
							.ButtonStyle(FCoreStyle::Get(), "NoBorder")
							.ToolTipText(LOCTEXT("BookmarkTip", "Toggle bookmark"))
							.OnClicked(this, &SBlendkitAssetTile::OnBookmarkClicked)
							[
								SNew(STextBlock)
								.Text_Lambda([this]()
								{
									return FText::FromString((Item.IsValid() && Item->bBookmarked) ? TEXT("\u2605") : TEXT("\u2606"));
								})
							]
						]
					]
					+ SVerticalBox::Slot()
					.AutoHeight()
					.Padding(0.f, 2.f, 0.f, 0.f)
					[
						SNew(STextBlock)
						.Text(FText::FromString(Item.IsValid() ? Item->Name : FString()))
						.Font(FCoreStyle::GetDefaultFontStyle("Regular", 8))
						.Justification(ETextJustify::Center)
						.WrapTextAt(TileWidth - 12.f)
					]
				]
			]
		];
	}

	virtual FReply OnMouseButtonDown(const FGeometry&, const FPointerEvent& Event) override
	{
		if (Event.GetEffectingButton() == EKeys::LeftMouseButton)
		{
			return FReply::Handled().DetectDrag(SharedThis(this), EKeys::LeftMouseButton);
		}
		if (Event.GetEffectingButton() == EKeys::RightMouseButton)
		{
			return FReply::Handled();
		}
		return FReply::Unhandled();
	}

	virtual FReply OnDragDetected(const FGeometry&, const FPointerEvent&) override
	{
		if (Item.IsValid())
		{
			if (UBlendkitBridge* Bridge = UBlendkitBridge::Get())
			{
				Bridge->OnAssetDragStarted.Broadcast(Item->AssetId);
			}
		}
		// Release capture so the level viewport receives the mouse moves and
		// repaints the drag preview while the button is held (the Python
		// DragSession tracks the cursor globally, so it doesn't need capture).
		return FReply::Handled().ReleaseMouseCapture();
	}

	virtual FReply OnMouseButtonDoubleClick(const FGeometry&, const FPointerEvent&) override
	{
		if (Item.IsValid())
		{
			OpenDetailWindow();
		}
		return FReply::Handled();
	}

	virtual FReply OnMouseButtonUp(const FGeometry&, const FPointerEvent& Event) override
	{
		if (Event.GetEffectingButton() == EKeys::RightMouseButton && Item.IsValid())
		{
			FMenuBuilder Menu(/*bShouldCloseWindowAfterMenuSelection*/ true, nullptr);
			const FString AssetId = Item->AssetId;
			const bool bBookmarked = Item->bBookmarked;

			Menu.AddMenuEntry(
				LOCTEXT("ViewDetails", "View details…"),
				LOCTEXT("ViewDetailsTip", "Open the asset detail popup with larger preview and information"),
				FSlateIcon(),
				FUIAction(FExecuteAction::CreateSP(this, &SBlendkitAssetTile::OpenDetailWindow)));

			Menu.AddSeparator();

			Menu.AddMenuEntry(
				bBookmarked ? LOCTEXT("RemoveBookmark", "Remove bookmark") : LOCTEXT("AddBookmark", "Add bookmark"),
				FText::GetEmpty(), FSlateIcon(),
				FUIAction(FExecuteAction::CreateLambda([AssetId]()
				{
					if (UBlendkitBridge* Bridge = UBlendkitBridge::Get())
					{
						Bridge->OnBookmarkToggled.Broadcast(AssetId);
					}
				})));

			if (!Item->AuthorName.IsEmpty())
			{
				const FString AuthorQuery = Item->AuthorId.IsEmpty() ? Item->AuthorName : FString::Printf(TEXT("author_id:%s"), *Item->AuthorId);
				const FString Type = Item->AssetType;
				Menu.AddMenuEntry(
					FText::Format(LOCTEXT("ShowMoreByAuthor", "More by '{0}'"), FText::FromString(Item->AuthorName)),
					LOCTEXT("ShowMoreByAuthorTip", "Search for assets created by this author"),
					FSlateIcon(),
					FUIAction(FExecuteAction::CreateLambda([AuthorQuery, Type]()
					{
						if (UBlendkitBridge* Bridge = UBlendkitBridge::Get())
						{
							Bridge->OnSearchRequested.Broadcast(AuthorQuery, Type);
						}
					})));
			}

			Menu.AddMenuEntry(
				LOCTEXT("OpenWebsite", "Open on blendkit.org"),
				FText::GetEmpty(), FSlateIcon(),
				FUIAction(FExecuteAction::CreateLambda([AssetId]()
				{
					if (UBlendkitBridge* Bridge = UBlendkitBridge::Get())
					{
						Bridge->OnAssetActivated.Broadcast(AssetId);
					}
				})));

			Menu.AddSeparator();

			const FString AssetName = Item->Name;
			Menu.AddMenuEntry(
				LOCTEXT("CopyName", "Copy name"),
				FText::GetEmpty(), FSlateIcon(),
				FUIAction(FExecuteAction::CreateLambda([AssetName]()
				{
					FPlatformApplicationMisc::ClipboardCopy(*AssetName);
				})));

			Menu.AddMenuEntry(
				LOCTEXT("CopyAssetId", "Copy asset ID"),
				FText::GetEmpty(), FSlateIcon(),
				FUIAction(FExecuteAction::CreateLambda([AssetId]()
				{
					FPlatformApplicationMisc::ClipboardCopy(*AssetId);
				})));

			FWidgetPath WidgetPath;
			FSlateApplication::Get().GeneratePathToWidgetUnchecked(SharedThis(this), WidgetPath);
			FSlateApplication::Get().PushMenu(
				SharedThis(this), WidgetPath, Menu.MakeWidget(),
				Event.GetScreenSpacePosition(),
				FPopupTransitionEffect(FPopupTransitionEffect::ContextMenu));
			return FReply::Handled();
		}
		return FReply::Unhandled();
	}

	void OpenDetailWindow()
	{
		if (!Item.IsValid())
		{
			return;
		}

		TSharedRef<SWindow> Window = SNew(SWindow)
			.Title(FText::Format(LOCTEXT("AssetDetailTitle", "Blendkit — {0}"), FText::FromString(Item->Name)))
			.ClientSize(FVector2D(540.f, 520.f))
			.SupportsMaximize(false)
			.SupportsMinimize(false)
			.SizingRule(ESizingRule::UserSized);

		Window->SetContent(
			SNew(SBlendkitAssetDetail)
			.Item(Item)
			.ParentWindow(Window)
		);

		TSharedPtr<SWindow> ParentWindow = FSlateApplication::Get().FindWidgetWindow(SharedThis(this));
		if (ParentWindow.IsValid())
		{
			FSlateApplication::Get().AddWindowAsNativeChild(Window, ParentWindow.ToSharedRef());
		}
		else
		{
			FSlateApplication::Get().AddWindow(Window);
		}
	}

private:
	FReply OnBookmarkClicked()
	{
		if (Item.IsValid())
		{
			if (UBlendkitBridge* Bridge = UBlendkitBridge::Get())
			{
				Bridge->OnBookmarkToggled.Broadcast(Item->AssetId);
			}
		}
		return FReply::Handled();
	}

	TSharedPtr<FBlendkitTileItem> Item;
};

// ---------------------------------------------------------------------------
// Asset bar
// ---------------------------------------------------------------------------

void SBlendkitAssetBar::Construct(const FArguments& InArgs)
{
	AssetTypes = {
		MakeShared<FString>(TEXT("model")),
		MakeShared<FString>(TEXT("material")),
		MakeShared<FString>(TEXT("scene")),
		MakeShared<FString>(TEXT("hdr")),
		MakeShared<FString>(TEXT("printable")),
	};
	CurrentType = AssetTypes[0];

	ChildSlot
	[
		SNew(SVerticalBox)
		// Search row.
		+ SVerticalBox::Slot()
		.AutoHeight()
		.Padding(4.f)
		[
			SNew(SHorizontalBox)
			+ SHorizontalBox::Slot()
			.AutoWidth()
			.VAlign(VAlign_Center)
			.Padding(0.f, 0.f, 4.f, 0.f)
			[
				SNew(SComboBox<TSharedPtr<FString>>)
				.OptionsSource(&AssetTypes)
				.InitiallySelectedItem(CurrentType)
				.OnGenerateWidget_Lambda([](TSharedPtr<FString> In)
				{
					return SNew(STextBlock).Text(FText::FromString(In.IsValid() ? *In : FString()));
				})
				.OnSelectionChanged_Lambda([this](TSharedPtr<FString> In, ESelectInfo::Type)
				{
					if (In.IsValid() && In != CurrentType)
					{
						CurrentType = In;
						TriggerSearch();
					}
				})
				[
					SNew(STextBlock)
					.Text_Lambda([this]()
					{
						return FText::FromString(CurrentType.IsValid() ? *CurrentType : FString());
					})
				]
			]
			+ SHorizontalBox::Slot()
			.FillWidth(1.f)
			.VAlign(VAlign_Center)
			[
				SAssignNew(SearchBox, SSearchBox)
				.HintText(LOCTEXT("SearchHint", "Search Blendkit…"))
				.OnTextCommitted(this, &SBlendkitAssetBar::OnSearchCommitted)
				.OnSearchGlassClicked_Lambda([this]() { TriggerSearch(); return FReply::Handled(); })
			]
		]
		// Status line.
		+ SVerticalBox::Slot()
		.AutoHeight()
		.Padding(6.f, 0.f, 6.f, 4.f)
		[
			SAssignNew(StatusText, STextBlock)
			.Text(FText::GetEmpty())
			.Font(FCoreStyle::GetDefaultFontStyle("Regular", 8))
		]
		// Grid.
		+ SVerticalBox::Slot()
		.FillHeight(1.f)
		[
			SAssignNew(TileView, STileView<TSharedPtr<FBlendkitTileItem>>)
			.ListItemsSource(&Items)
			.OnGenerateTile(this, &SBlendkitAssetBar::OnGenerateTile)
			.OnTileViewScrolled(this, &SBlendkitAssetBar::OnListScrolled)
			.ItemWidth(TileWidth)
			.ItemHeight(TileHeight)
			.SelectionMode(ESelectionMode::None)
		]
	];

	if (UBlendkitBridge* Bridge = UBlendkitBridge::Get())
	{
		ResultsHandle = Bridge->OnResultsChanged.AddSP(this, &SBlendkitAssetBar::RebuildItems);
		ThumbHandle = Bridge->OnThumbnailChanged.AddSP(this, &SBlendkitAssetBar::OnThumbnailChanged);
		StatusHandle = Bridge->OnStatusChanged.AddSP(this, &SBlendkitAssetBar::OnStatusChanged);
		RebuildItems();
		OnStatusChanged(Bridge->GetStatus());
	}
}

SBlendkitAssetBar::~SBlendkitAssetBar()
{
	if (UBlendkitBridge* Bridge = UBlendkitBridge::Get())
	{
		Bridge->OnResultsChanged.Remove(ResultsHandle);
		Bridge->OnThumbnailChanged.Remove(ThumbHandle);
		Bridge->OnStatusChanged.Remove(StatusHandle);
	}
}

void SBlendkitAssetBar::Tick(const FGeometry& AllottedGeometry, const double InCurrentTime, const float InDeltaTime)
{
	SCompoundWidget::Tick(AllottedGeometry, InCurrentTime, InDeltaTime);

	// A DPI/layout scale change (e.g. maximize/restore on macOS) leaves the
	// STileView measuring items at the old scale until something re-lays it
	// out; refresh it ourselves so we don't need a manual window resize.
	const float Scale = AllottedGeometry.Scale;
	if (!FMath::IsNearlyEqual(Scale, LastLayoutScale))
	{
		LastLayoutScale = Scale;
		if (TileView.IsValid())
		{
			TileView->RequestListRefresh();
		}
	}
}

void SBlendkitAssetBar::RebuildItems()
{
	// Reuse existing tiles by asset id so appended pages keep already-decoded
	// thumbnails and the scroll position is preserved.
	TMap<FString, TSharedPtr<FBlendkitTileItem>> Existing;
	for (const TSharedPtr<FBlendkitTileItem>& Tile : Items)
	{
		if (Tile.IsValid())
		{
			Existing.Add(Tile->AssetId, Tile);
		}
	}

	Items.Reset();

	UBlendkitBridge* Bridge = UBlendkitBridge::Get();
	if (Bridge)
	{
		for (const FBlendkitAssetItem& Src : Bridge->GetResults())
		{
			TSharedPtr<FBlendkitTileItem> Tile = Existing.FindRef(Src.AssetId);
			if (!Tile.IsValid())
			{
				Tile = MakeShared<FBlendkitTileItem>();
				Tile->AssetId = Src.AssetId;
				Tile->AssetType = Src.AssetType;
				Tile->ThumbnailPath = Src.ThumbnailPath;
				Tile->Brush = MakeImageBrush(Src.ThumbnailPath);
			}
			// Refresh the mutable fields either way.
			Tile->Name = Src.Name;
			Tile->bCanDownload = Src.bCanDownload;
			Tile->bBookmarked = Src.bBookmarked;
			Tile->Description = Src.Description;
			Tile->AuthorName = Src.AuthorName;
			Tile->AuthorId = Src.AuthorId;
			Tile->License = Src.License;
			Tile->Price = Src.Price;
			Tile->Rating = Src.Rating;
			Tile->DownloadCount = Src.DownloadCount;
			Tile->Tags = Src.Tags;
			if (!Tile->Brush.IsValid() && !Src.ThumbnailPath.IsEmpty())
			{
				Tile->ThumbnailPath = Src.ThumbnailPath;
				Tile->Brush = MakeImageBrush(Src.ThumbnailPath);
			}
			Items.Add(Tile);
		}
	}

	if (TileView.IsValid())
	{
		TileView->RequestListRefresh();
	}
}

void SBlendkitAssetBar::OnListScrolled(double /*ScrollOffset*/)
{
	if (!TileView.IsValid() || Items.Num() == 0)
	{
		return;
	}
	// Y component is the normalized distance still scrollable below the view.
	if (TileView->GetScrollDistanceRemaining().Y <= 0.15f)
	{
		if (UBlendkitBridge* Bridge = UBlendkitBridge::Get())
		{
			Bridge->OnLoadMoreRequested.Broadcast();
		}
	}
}

void SBlendkitAssetBar::OnThumbnailChanged(const FString& AssetId, const FString& Path)
{
	for (TSharedPtr<FBlendkitTileItem>& Tile : Items)
	{
		if (Tile.IsValid() && Tile->AssetId == AssetId)
		{
			Tile->ThumbnailPath = Path;
			Tile->Brush = MakeImageBrush(Path);
			break;
		}
	}
	if (TileView.IsValid())
	{
		TileView->RequestListRefresh();
	}
}

void SBlendkitAssetBar::OnStatusChanged(const FString& Text)
{
	if (StatusText.IsValid())
	{
		StatusText->SetText(FText::FromString(Text));
	}
}

TSharedRef<ITableRow> SBlendkitAssetBar::OnGenerateTile(TSharedPtr<FBlendkitTileItem> Item, const TSharedRef<STableViewBase>& Owner)
{
	return SNew(STableRow<TSharedPtr<FBlendkitTileItem>>, Owner)
		.Padding(0.f)
		[
			SNew(SBlendkitAssetTile).Item(Item)
		];
}

void SBlendkitAssetBar::OnSearchCommitted(const FText& Text, ETextCommit::Type CommitType)
{
	if (CommitType == ETextCommit::OnEnter)
	{
		TriggerSearch();
	}
}

void SBlendkitAssetBar::TriggerSearch()
{
	if (UBlendkitBridge* Bridge = UBlendkitBridge::Get())
	{
		const FString Query = SearchBox.IsValid() ? SearchBox->GetText().ToString() : FString();
		const FString Type = CurrentType.IsValid() ? *CurrentType : FString(TEXT("model"));
		Bridge->OnSearchRequested.Broadcast(Query, Type);
	}
}

#undef LOCTEXT_NAMESPACE
