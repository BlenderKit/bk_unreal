#include "SBlendkitSettings.h"

#include "BlendkitBridge.h"
#include "DesktopPlatformModule.h"
#include "Framework/Application/SlateApplication.h"
#include "IDesktopPlatform.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SCheckBox.h"
#include "Widgets/Input/SComboBox.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "BlendkitSettings"

namespace
{
	TSharedRef<SWidget> SettingsRow(const FText& Label, const TSharedRef<SWidget>& Control)
	{
		return SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 8.f, 0.f, 3.f)
			[
				SNew(STextBlock).Text(Label)
			]
			+ SVerticalBox::Slot().AutoHeight()
			[
				Control
			];
	}
}

void SBlendkitSettings::Construct(const FArguments& InArgs)
{
	const FBlendkitSettings& Settings = UBlendkitBridge::Get()->GetSettings();
	for (const TCHAR* Value : { TEXT("512"), TEXT("1024"), TEXT("2048"), TEXT("4096"), TEXT("8192"), TEXT("ORIGINAL") })
	{
		TSharedPtr<FString> Option = MakeShared<FString>(Value);
		Resolutions.Add(Option);
		if (*Option == Settings.Resolution)
		{
			Resolution = Option;
		}
	}
	if (!Resolution.IsValid())
	{
		Resolution = Resolutions.Last();
	}

	ChildSlot
	[
		SNew(SScrollBox)
		+ SScrollBox::Slot().Padding(12.f)
		[
			SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight()
			[
				SNew(STextBlock)
				.Text_Lambda([] { return FText::FromString(UBlendkitBridge::Get()->GetSettings().AccountStatus); })
				.AutoWrapText(true)
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 6.f)
			[
				SNew(SHorizontalBox)
				+ SHorizontalBox::Slot().AutoWidth()
				[
					SNew(SButton)
					.Text(LOCTEXT("Login", "Log In via Browser"))
					.IsEnabled_Lambda([]
					{
						const auto& State = UBlendkitBridge::Get()->GetSettings();
						return !State.bLoggedIn && !State.bLoginPending;
					})
					.OnClicked_Lambda([]
					{
						UBlendkitBridge::Get()->OnLoginRequested.Broadcast();
						return FReply::Handled();
					})
				]
				+ SHorizontalBox::Slot().AutoWidth().Padding(6.f, 0.f)
				[
					SNew(SButton)
					.Text(LOCTEXT("Logout", "Log Out"))
					.IsEnabled_Lambda([] { return UBlendkitBridge::Get()->GetSettings().bLoggedIn; })
					.OnClicked_Lambda([]
					{
						UBlendkitBridge::Get()->OnLogoutRequested.Broadcast();
						return FReply::Handled();
					})
				]
			]
			+ SVerticalBox::Slot().AutoHeight()
			[
				SettingsRow(LOCTEXT("ApiKey", "API key"),
					SNew(SHorizontalBox)
					+ SHorizontalBox::Slot().FillWidth(1.f)
					[
						SAssignNew(ApiKey, SEditableTextBox).IsPassword(true)
					]
					+ SHorizontalBox::Slot().AutoWidth().Padding(6.f, 0.f, 0.f, 0.f)
					[
						SNew(SButton).Text(LOCTEXT("Apply", "Apply")).OnClicked(this, &SBlendkitSettings::ApplyKey)
					])
			]
			+ SVerticalBox::Slot().AutoHeight()
			[
				SettingsRow(LOCTEXT("DataDirectory", "Data directory"),
					SNew(SHorizontalBox)
					+ SHorizontalBox::Slot().FillWidth(1.f)
					[
						SAssignNew(DataDirectory, SEditableTextBox).Text(FText::FromString(Settings.GlobalDir))
					]
					+ SHorizontalBox::Slot().AutoWidth().Padding(6.f, 0.f, 0.f, 0.f)
					[
						SNew(SButton).Text(LOCTEXT("BrowseDirectory", "Browse"))
						.OnClicked(this, &SBlendkitSettings::BrowseDirectory)
					])
			]
			+ SVerticalBox::Slot().AutoHeight()
			[
				SettingsRow(LOCTEXT("Resolution", "Import resolution"),
					SAssignNew(ResolutionBox, SComboBox<TSharedPtr<FString>>)
					.OptionsSource(&Resolutions)
					.InitiallySelectedItem(Resolution)
					.OnGenerateWidget_Lambda([](TSharedPtr<FString> Value)
					{
						return SNew(STextBlock).Text(FText::FromString(*Value));
					})
					.OnSelectionChanged_Lambda([this](TSharedPtr<FString> Value, ESelectInfo::Type)
					{
						Resolution = Value;
					})
					[
						SNew(STextBlock).Text_Lambda([this] { return FText::FromString(*Resolution); })
					])
			]
			+ SVerticalBox::Slot().AutoHeight()
			[
				SettingsRow(LOCTEXT("BlenderExecutable", "Blender executable"),
					SNew(SHorizontalBox)
					+ SHorizontalBox::Slot().FillWidth(1.f)
					[
						SAssignNew(BlenderExecutable, SEditableTextBox)
						.Text(FText::FromString(Settings.BlenderExecutable))
					]
					+ SHorizontalBox::Slot().AutoWidth().Padding(6.f, 0.f, 0.f, 0.f)
					[
						SNew(SButton).Text(LOCTEXT("BrowseBlender", "Browse"))
						.OnClicked(this, &SBlendkitSettings::BrowseBlender)
					]
					+ SHorizontalBox::Slot().AutoWidth().Padding(6.f, 0.f, 0.f, 0.f)
					[
						SNew(SButton).Text(LOCTEXT("DetectBlender", "Detect"))
						.IsEnabled_Lambda([] { return !UBlendkitBridge::Get()->GetSettings().bBlenderPending; })
						.OnClicked(this, &SBlendkitSettings::DetectBlender)
					])
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 4.f)
			[
				SNew(STextBlock)
				.Text_Lambda([] { return FText::FromString(UBlendkitBridge::Get()->GetSettings().BlenderStatus); })
				.AutoWrapText(true)
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 8.f)
			[
				SAssignNew(VerifySsl, SCheckBox)
				.IsChecked(Settings.bVerifySsl ? ECheckBoxState::Checked : ECheckBoxState::Unchecked)
				[
					SNew(STextBlock).Text(LOCTEXT("VerifySsl", "Verify SSL certificates"))
				]
			]
			+ SVerticalBox::Slot().AutoHeight().Padding(0.f, 8.f)
			[
				SNew(SButton).Text(LOCTEXT("Save", "Save")).OnClicked(this, &SBlendkitSettings::Save)
			]
			+ SVerticalBox::Slot().AutoHeight()
			[
				SNew(STextBlock)
				.Text_Lambda([] { return FText::FromString(UBlendkitBridge::Get()->GetSettings().Message); })
				.AutoWrapText(true)
			]
		]
	];
}

FReply SBlendkitSettings::Save()
{
	FBlendkitSettings Settings = UBlendkitBridge::Get()->GetSettings();
	Settings.GlobalDir = DataDirectory->GetText().ToString();
	Settings.BlenderExecutable = BlenderExecutable->GetText().ToString();
	Settings.Resolution = *Resolution;
	Settings.bVerifySsl = VerifySsl->IsChecked();
	UBlendkitBridge::Get()->OnSettingsSaved.Broadcast(Settings);
	return FReply::Handled();
}

FReply SBlendkitSettings::BrowseDirectory()
{
	IDesktopPlatform* Desktop = FDesktopPlatformModule::Get();
	FString Chosen;
	if (Desktop && Desktop->OpenDirectoryDialog(
		FSlateApplication::Get().FindBestParentWindowHandleForDialogs(SharedThis(this)),
		TEXT("Blendkit data directory"), DataDirectory->GetText().ToString(), Chosen))
	{
		DataDirectory->SetText(FText::FromString(Chosen));
	}
	return FReply::Handled();
}

FReply SBlendkitSettings::BrowseBlender()
{
	IDesktopPlatform* Desktop = FDesktopPlatformModule::Get();
	TArray<FString> Chosen;
	if (Desktop && Desktop->OpenFileDialog(
		FSlateApplication::Get().FindBestParentWindowHandleForDialogs(SharedThis(this)),
		TEXT("Select Blender executable"), FPaths::GetPath(BlenderExecutable->GetText().ToString()),
		FString(), TEXT("All files (*.*)|*.*"), EFileDialogFlags::None, Chosen) && !Chosen.IsEmpty())
	{
		BlenderExecutable->SetText(FText::FromString(Chosen[0]));
		DetectBlender();
	}
	return FReply::Handled();
}

FReply SBlendkitSettings::DetectBlender()
{
	UBlendkitBridge::Get()->OnBlenderDetectRequested.Broadcast(BlenderExecutable->GetText().ToString());
	return FReply::Handled();
}

FReply SBlendkitSettings::ApplyKey()
{
	const FString Value = ApiKey->GetText().ToString();
	ApiKey->SetText(FText::GetEmpty());
	UBlendkitBridge::Get()->OnManualKeyRequested.Broadcast(Value);
	return FReply::Handled();
}

#undef LOCTEXT_NAMESPACE