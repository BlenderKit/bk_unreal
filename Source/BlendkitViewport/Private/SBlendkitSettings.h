#pragma once

#include "CoreMinimal.h"
#include "Widgets/SCompoundWidget.h"

class SCheckBox;
class SEditableTextBox;
template <typename OptionType> class SComboBox;

class SBlendkitSettings : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SBlendkitSettings) {}
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs);

private:
	FReply Save();
	FReply BrowseDirectory();
	FReply BrowseBlender();
	FReply DetectBlender();
	FReply ApplyKey();

	TSharedPtr<SEditableTextBox> DataDirectory;
	TSharedPtr<SEditableTextBox> BlenderExecutable;
	TSharedPtr<SEditableTextBox> ApiKey;
	TSharedPtr<SCheckBox> VerifySsl;
	TSharedPtr<SComboBox<TSharedPtr<FString>>> ResolutionBox;
	TArray<TSharedPtr<FString>> Resolutions;
	TSharedPtr<FString> Resolution;
};